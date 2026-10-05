"""The coordinator: one bounded, resumable state machine over components that already exist.

It is deliberately NOT a decompilation engine. Every stage delegates:

    frozen            -> `eval.generation_manifest.freeze`
    research          -> `eval.local_research.Research` (the existing worker, given a real cluster)
    verify            -> the worker's own notebook + `verdict()` result
    assemble          -> `eval.rsi_interventions` (a proposed note, never a behaviour change)
    candidate-frozen  -> `eval.generation_manifest.freeze` with `parent=S0`
    evaluate          -> `eval.rsi_transfer.paired_transfer`
    accept/keep       -> the preregistered gate in this file

RESUME IS IDEMPOTENT BY CONSTRUCTION. Each stage writes its own receipt under `stages/`; a stage whose
receipt exists is not re-run, so resuming after an interruption cannot publish twice, retrain twice or
reset a spent budget. Partial artifacts are kept and the generation is NOT called complete: the
generation pointer moves only through `accept`, and only after a stage receipt exists for every earlier
stage.

WHAT THE GATE IS. Preregistered and deliberately strict: the intervention must not lose a certified
match, must not change the project's production match count, and must GAIN at least one certified match
on the sealed panel to advance. A procedure-only gain is reported as a procedure result and keeps the
parent -- it does not promote a generation, because promoting on a secondary measure is how a report
starts claiming decompilation progress it does not have.

STOP. A stop file is checked between stages and inside the research stage. Stopping preserves receipts,
marks the state `stopped`, and never auto-restarts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("frozen", "research", "verify", "assemble", "candidate-frozen", "evaluate", "decide")
LEGACY_STAGE_DIR = "stages"          # the pre-round layout, kept read-only as history

DEFAULT_CONFIG = {
    "experiment_id": "rsi-smoke-1",
    "schema_version": 1,
    "rounds": 1,
    "researcher": {"model": "qwen2.5-coder-14b", "minutes": 20, "max_calls": 6},
    "caps": {"model_calls": 12, "compiles": 72, "seconds": 1800.0,
             "panel_functions": 6, "per_task_actions": 5},
    "panel": {"split": "dev", "functions": 6},
    "gate": {"require_certified_gain": 1, "forbid_certified_loss": True},
    "downstream": {"arm": "adapter", "adapter": "/home/grant/decomp/models/adapters/tool-action-v2/adapter",
                   "base": "/home/grant/decomp/models/qwen2.5-coder-7b"},
    "cluster_index": 2,
}


class Stopped(RuntimeError):
    pass


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _gate_rows(rows: list) -> dict:
    """Per-task rows in the shape `posttraining_gate.decide` reads, with errors kept out of the draws.

    AN INFRASTRUCTURE ERROR IS NOT A COMPLETED DRAW. The previous version wrote `draws: 1` for every
    row that had a function name, so a task whose setup raised became a task that ran and did not
    close -- indistinguishable from a task the adapter failed to close, and invisible to R5. A row
    carrying `error` is passed through with NO draw count, which R5 already rejects as "the row
    records no draw count", and with the error text so the receipt names the cause.
    """
    out: dict = {}
    for row in rows or []:
        name = row.get("function")
        if not name:
            continue
        if row.get("error"):
            out[name] = {"exact": False, "error": str(row["error"])[:300]}
            continue
        out[name] = {"exact": bool(row.get("exact")), "draws": 1}
    return out


class Experiment:
    def __init__(self, root: Path, config: dict):
        # ABSOLUTE PATHS OR NOTHING. The research worker hands the compiler a WORK DIRECTORY built from
        # its `state` path, and the compiler runs as a subprocess in a different working directory. A
        # relative root therefore produced `FileNotFoundError: .../probe.c` on the very first probe, the
        # experiment was recorded `compile_failed`, and the loop reported "no confirmed finding" -- a
        # capability-shaped null caused entirely by a path. `local_research.main` guards this for its own
        # CLI (it rejects /mnt paths) but the coordinator calls the worker in-process, so nothing was
        # checking. Resolved once, here, for every path the experiment derives.
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = {**DEFAULT_CONFIG, **config}
        # WHAT IDENTIFIES THIS RUN, hashed once. A stage receipt is only reusable by a run that
        # declared the same thing; without this a cached receipt from a differently-configured run
        # is indistinguishable from one this run wrote, and the stage is silently skipped.
        self.config_sha256 = hashlib.sha256(
            json.dumps(self.config, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        self.rounds_declared = max(1, int(self.config.get("rounds", 1) or 1))
        self.legacy_receipts: set[str] = set()
        self.state_path = self.root / "state.json"
        self.view_path = self.root / "rsi_state.json"     # the dashboard contract
        self.stop_path = self.root / "STOP"
        # Fixed by the coordinator so the stop file can name the worker that is actually running.
        self.worker_run_id = f"{self.config['experiment_id']}-research"
        self.events_path = self.root / "events.jsonl"
        self.state = _read(self.state_path, {}) or {
            "experiment_id": self.config["experiment_id"], "stage": "frozen",
            "generation": "S0", "candidate": None, "parent": None, "round": 1,
            "hypothesis": "", "evidence_status": "unknown", "gate_reason": "",
            "started_at": time.time(), "schema_version": self.config["schema_version"],
        }
        # A GENERATION IS DERIVED FROM THE ROUND, never written as a literal. The first version
        # hardcoded S0/S1 in five places, so `rounds: 2` ran one generation twice and the parent
        # pointer never moved: the second round re-used S1's id and re-reported round 1's verdict.
        self.state["round"] = int(self.state.get("round") or 1)
        from eval.budget_ledger import Caps, Ledger
        caps = Caps(**{k: v for k, v in self.config["caps"].items()})
        self.ledger = Ledger.open(self.root / "budget.jsonl", caps)

    # --- round and generation identity --------------------------------------

    @property
    def round(self) -> int:
        return int(self.state.get("round") or 1)

    @property
    def parent_id(self) -> str:
        """The generation this round starts from: the round number, minus one."""
        return f"S{self.round - 1}"

    @property
    def candidate_id(self) -> str:
        """The generation this round would produce."""
        return f"S{self.round}"

    def stages_dir(self, round_number: int | None = None) -> Path:
        """Receipts are scoped to the round that wrote them.

        THE DEFECT THIS PINS. `stages/{stage}.json` is one path for every round, so round 2 found
        round 1's receipts, skipped every stage as `stage_skipped_resumed`, and published round 1's
        verdict as round 2's result -- a second generation that never ran and cannot be told apart
        from one that did.
        """
        return self.root / "stages" / f"r{round_number if round_number is not None else self.round}"

    # --- plumbing -----------------------------------------------------------

    def event(self, kind: str, **fields) -> None:
        row = {"event": kind, "at": time.time(), "stage": self.state.get("stage"), **fields}
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def publish(self) -> None:
        self.state["updated_at"] = time.time()
        self.state["budget"] = self.ledger.snapshot()
        self.state["stop_requested"] = self.stop_path.exists()
        self.state["rounds_declared"] = self.rounds_declared
        if self.legacy_receipts:
            # THE OLD LAYOUT IS MARKED, NOT REWRITTEN. Receipts written before receipts were
            # round-scoped cannot say which round they belong to, so they are reused for round 1 and
            # named here as historical. Rewriting them into the new layout would fabricate a
            # provenance nobody observed.
            self.state["legacy_receipts"] = sorted(self.legacy_receipts)
        _write(self.state_path, self.state)
        _write(self.view_path, self.state)

    def provenance_mismatch(self, payload: dict, stage: str) -> list[str]:
        """Why this receipt does not belong to the run/round that is asking for it.

        A receipt with NO provenance is a pre-round-scoped one: accepted for round 1 and marked
        historical by the caller. A receipt WITH provenance must agree about the experiment, the
        config, the round, the generation pair and the stage, or it is not this stage's receipt.
        """
        provenance = payload.get("provenance")
        if not provenance:
            return []
        problems = []
        if provenance.get("experiment_id") != self.config["experiment_id"]:
            problems.append(f"experiment_id {provenance.get('experiment_id')!r} != "
                            f"{self.config['experiment_id']!r}")
        if provenance.get("config_sha256") != self.config_sha256:
            problems.append("the run configuration changed since this receipt was written")
        if int(provenance.get("round") or 0) != self.round:
            problems.append(f"round {provenance.get('round')} != {self.round}")
        if provenance.get("generation") != self.parent_id:
            problems.append(f"generation {provenance.get('generation')!r} != {self.parent_id!r}")
        if provenance.get("candidate") != self.candidate_id:
            problems.append(f"candidate {provenance.get('candidate')!r} != {self.candidate_id!r}")
        if provenance.get("stage") != stage:
            problems.append(f"stage {provenance.get('stage')!r} != {stage!r}")
        return problems

    def receipt(self, stage: str) -> dict | None:
        path = self.stages_dir() / f"{stage}.json"
        payload = _read(path)
        if payload is not None:
            mismatch = self.provenance_mismatch(payload, stage)
            if mismatch:
                # REJECTED, NOT REUSED. This is the difference between "the stage is done" and "a
                # stage with this name is done": the stage re-runs under the identity that asks for
                # it, and the rejection is on the record.
                self.event("stage_receipt_rejected", stage=stage, path=str(path),
                           mismatch=mismatch)
                return None
            return payload
        legacy_path = self.root / LEGACY_STAGE_DIR / f"{stage}.json"
        legacy = _read(legacy_path)
        if legacy is not None and self.round == 1:
            self.legacy_receipts.add(stage)
            return {**legacy, "_legacy_path": str(legacy_path), "_round_scoped": False}
        return None

    def record(self, stage: str, payload: dict) -> dict:
        payload = {**payload, "stage": stage, "at": time.time(),
                   # EVERY RECEIPT NAMES THE RUN, THE ROUND AND THE GENERATION PAIR IT BELONGS TO.
                   "provenance": {"experiment_id": self.config["experiment_id"],
                                  "config_sha256": self.config_sha256,
                                  "round": self.round, "generation": self.parent_id,
                                  "candidate": self.candidate_id, "stage": stage,
                                  "schema_version": self.config["schema_version"]}}
        _write(self.stages_dir() / f"{stage}.json", payload)
        self.event(f"stage_{stage}_done", **{k: v for k, v in payload.items()
                                             if k not in ("rows", "raw")})
        return payload

    def check_stop(self) -> None:
        """Honour the operator's stop and the worker's own contract.

        TWO FILES, ONE INTENT. `STOP` is the marker the dashboard writes; `stop.json` is what
        `eval.local_research` polls, and it only stops when the file's `run_id` matches the worker's.
        Translating the marker into the worker's contract here means one stop control works for the
        whole experiment -- and the worker still gets the graceful, receipt-preserving path rather than
        a signal.
        """
        if self.stop_path.exists():
            worker_stop = self.root / "stop.json"
            worker_stop.parent.mkdir(parents=True, exist_ok=True)
            worker_stop.write_text(json.dumps({"run_id": self.worker_run_id}), encoding="utf-8")
            raise Stopped("stop requested by the operator")
        self.ledger.guard_seconds()

    def set_stage(self, stage: str, **fields) -> None:
        self.state.update(stage=stage, **fields)
        self.publish()

    # --- stages -------------------------------------------------------------

    def declared_panel(self) -> list[str]:
        """The evaluation panel, DECLARED FROM THE SPLIT before any setup runs.

        THE DEFECT THIS PINS. The gate's expected task list was `evaluation["usable"]` -- the tasks
        whose context happened to build. A run that declared 13 tasks and set up 12 therefore handed
        the gate a 12-task specification, coverage was measured against the 12 that survived, and the
        task lost to an infrastructure failure could not make the result incomplete. Expected coverage
        is a property of the FREEZE, never of the setup.
        """
        splits = _read(ROOT / "eval/results/tool-action-20260921/splits.json", {}) or {}
        split = self.config["panel"]["split"]
        names = list(splits.get(split) or [])
        if not names:
            raise SystemExit(
                f"the frozen split {split!r} has no functions in "
                f"eval/results/tool-action-20260921/splits.json, so there is no panel to declare; a "
                f"panel chosen after the run is not a frozen specification")
        return names[: int(self.config["panel"]["functions"])]

    def evaluation_spec(self) -> dict:
        """The frozen evaluation this round is supposed to execute, as data."""
        return {"expected_task_ids": self.declared_panel(),
                "split": self.config["panel"]["split"],
                # One episode per function per arm is what `paired_transfer` runs, and R5 compares the
                # ACTUAL draw count against this. Declaring it here rather than inferring it from the
                # rows is the difference between a budget and a description.
                "draws_per_task": 1,
                "kind": self.config["gate"].get("kind", "frozen"),
                "min_tasks": int(self.config["gate"].get("min_tasks", 12)),
                "manifest_sha256": ""}

    def stage_frozen(self) -> dict:
        """Freeze the parent generation AND declare the evaluation specification.

        THE SPECIFICATION IS FROZEN HERE, BEFORE ANY SETUP. `stage_evaluate` builds contexts and both
        arms then run; if the specification were derived from what those steps managed to produce, an
        infrastructure failure would silently redefine what the run promised to cover.
        """
        from eval import generation_manifest as gm
        from eval.tool_agent import AGENT_VERSION
        from eval.tool_agent_probe import SYSTEM
        from eval.tool_action_dataset import RENDERER_VERSION
        from eval.tool_registry import action_space

        spec = self.evaluation_spec()
        generations = self.root / "generations"
        existing = generations / f"{self.parent_id}.json"
        if existing.exists():
            # A PARENT THAT IS ALREADY FROZEN IS VERIFIED, NOT RE-FROZEN. Round 2's parent is round
            # 1's accepted child, whose manifest was written by `candidate-frozen` with a different
            # component set; re-freezing it from this method's template would either raise (it is a
            # different manifest under the same id) or, worse, quietly overwrite the identity the
            # previous round was accepted under.
            manifest = gm.load(generations, self.parent_id)
            verified, problems = None, []
            try:
                report = gm.verify(generations, self.parent_id)
                verified, problems = bool(report.get("verified")), list(report.get("problems") or [])
            except Exception as exc:                            # noqa: BLE001
                # NOT SILENT: an unverifiable parent is recorded as unverifiable, with the reason,
                # and the receipt does not claim the check passed.
                problems = [f"{type(exc).__name__}: {exc}"]
            return {"generation": self.parent_id, "manifest": str(existing),
                    "fingerprint": manifest.content_fingerprint(), "reused": True,
                    "parent": manifest.parent, "verified": verified, "problems": problems,
                    "spec": spec, "panel": spec["expected_task_ids"]}

        base = Path(self.config["downstream"]["base"])
        adapter = Path(self.config["downstream"]["adapter"])
        weights = sorted([p for p in base.glob("*.safetensors")] + [p for p in base.glob("*.json")])
        generation = gm.Generation(
            id=self.parent_id, created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            # The fresh branch is only reachable at round 1: any later round's parent was written by
            # the previous round's `candidate-frozen` and is loaded, not re-created.
            parent=None,
            base_model={"path": str(base), "files": [gm.hash_artifact(p) for p in weights]},
            adapters=[gm.hash_artifact(adapter / "adapter_config.json"),
                      gm.hash_artifact(adapter / "adapter_model.safetensors")],
            prompts={"system": {"sha256": gm.sha256_text(SYSTEM), "chars": len(SYSTEM)}},
            tool_schema={"action_space": {"sha256": gm.sha256_text(json.dumps(action_space(),
                                                                            sort_keys=True))}},
            # A COMPONENT THAT IS ABSENT ON PURPOSE SAYS SO. `{"exists": False}` alone is ambiguous --
            # it is emitted both for "there is no notes file yet, which is the normal state of S0" and
            # for "a file that was there has vanished" -- and the verifier cannot tell them apart from
            # the record. Naming the intent makes the check decidable instead of permanently alarming.
            memory={"path": str(self.root / "notes.jsonl"),
                    "exists": (self.root / "notes.jsonl").exists(),
                    "absent_because": None if (self.root / "notes.jsonl").exists()
                    else "no note has been written yet; S0 starts with an empty verified memory"},
            compositions=[],
            # TWO IDENTITIES, NOT ONE, AND NEITHER OVERWRITES THE OTHER. The previous literal spread
            # `**hash_artifact(solver/workspace.py)` after `"path": eval/tool_agent.py`, so the dict key
            # `path` was silently replaced and the manifest declared the VERIFIER to be workspace.py
            # while naming tool_agent.py nowhere. Both are execution-relevant: the loop decides what an
            # attempt is, the oracle decides whether it is exact.
            verifier={"loop": {"path": str(ROOT / "eval/tool_agent.py"),
                               **gm.hash_artifact(ROOT / "eval/tool_agent.py")},
                      "oracle": {"path": str(ROOT / "solver/workspace.py"),
                                 **gm.hash_artifact(ROOT / "solver/workspace.py")}},
            evaluator={"path": str(ROOT / "eval/rsi_transfer.py"),
                       **gm.hash_artifact(ROOT / "eval/rsi_transfer.py")},
            datasets={"splits": gm.hash_artifact(self.config["panel"].get(
                "splits", ROOT / "eval/results/tool-action-20260921/splits.json"))},
            budgets=self.ledger.caps.as_dict(),
            notes=f"agent_version={AGENT_VERSION} renderer={RENDERER_VERSION}")
        path = gm.freeze(generations, generation)
        # VERIFIED AS IT IS WRITTEN, not only when it is reused. A frozen generation whose receipt does
        # not say whether it verified is a claim nobody checked, and the fresh branch was the one that
        # skipped the check.
        verified, problems, status = None, [], None
        try:
            report = gm.verify(generations, self.parent_id)
            verified, problems = bool(report.get("verified")), list(report.get("problems") or [])
            status = report.get("status")
        except Exception as exc:                                # noqa: BLE001
            problems = [f"{type(exc).__name__}: {exc}"]
        return {"generation": self.parent_id, "manifest": str(path), "refrozen": True,
                "fingerprint": generation.content_fingerprint(),
                "files_hashed": len(weights) + 2, "spec": spec,
                "panel": spec["expected_task_ids"],
                "verified": verified, "problems": problems, "verify_status": status}

    def declared_spec(self) -> dict | None:
        """The specification frozen at the start of this round, read from the FROZEN receipt."""
        frozen = self.receipt("frozen") or {}
        spec = frozen.get("spec")
        return spec if isinstance(spec, dict) and spec.get("expected_task_ids") else None

    def stage_research(self) -> dict:
        """Run the EXISTING local research worker on a real failure cluster."""
        cluster = self.pick_cluster()
        self.researcher_cluster = cluster
        self.set_stage("research", hypothesis=f"cluster {cluster['cluster_id']}: "
                                              f"{cluster['error_class'][:80]}")
        # ROUND-SCOPED, because the worker keeps its notebook and state under this path: a second
        # round writing into the first round's directory would append its probes to the previous
        # round's notebook and the `verify` stage would read both as one evidence set.
        runs = self.root / "research" / f"r{self.round}"
        settings = self.config["researcher"]
        from eval.local_research import Halt, Research
        worker = Research(
            state=(runs / "state").resolve(), view=self.view_path.resolve(),
            stop=(self.root / "stop.json").resolve(),
            repo=Path(self.config["panel"].get("repo", Path.home() / "decomp/sbk1")).resolve(),
            model=settings["model"], minutes=settings["minutes"],
            max_calls=settings["max_calls"], cluster=cluster,
            # The run id is fixed by the COORDINATOR, not generated inside the worker, so the stop
            # file written by `check_stop` names the worker that is actually running.
            run_id=self.worker_run_id)
        # The experiment-wide ledger owns the cap; the worker's own budget is bounded by what remains,
        # so a second round cannot silently re-spend the first round's allowance.
        remaining = self.ledger.remaining()
        worker.budget.limits["calls"] = int(min(settings["max_calls"], remaining.get("model_calls", 0)))
        worker.budget.limits["compiles"] = int(min(settings["max_calls"] * 6,
                                                   remaining.get("compiles", 0)))
        if worker.budget.limits["calls"] <= 0 or worker.budget.limits["compiles"] <= 0:
            raise Stopped("the experiment-wide budget cannot fund a research call")
        budget = worker.budget
        original_publish = worker.publish

        def publish_with_spend(force=False):
            # The dashboard follows the worker's live counters; the LEDGER is charged once, at the end
            # of the stage, from the worker's own usage. Writing intermediate totals into `spent` would
            # double-count as soon as any other stage had spent anything.
            original_publish(force)
            self.state["research_used"] = dict(budget.used)
            self.publish()

        worker.publish = publish_with_spend
        result = worker.run()
        self.ledger.spend("research", model_calls=budget.used["calls"],
                          compiles=budget.used["compiles"])
        notebook = runs / "state" / "notebook.jsonl"
        rows = [json.loads(line) for line in notebook.read_text("utf-8").splitlines() if line.strip()] \
            if notebook.exists() else []
        return {"cluster": cluster, "status": result.get("status"),
                "calls": budget.used["calls"], "compiles": budget.used["compiles"],
                "notebook_rows": len(rows), "notebook": str(notebook),
                "confirmed": sum(1 for row in rows if row.get("status") == "synthetic_confirmed")}

    def pick_cluster(self) -> dict:
        queue = _read(self.root / "demand-queue.json")
        if not queue:
            raise SystemExit(f"no demand queue at {self.root/'demand-queue.json'}; run "
                             f"`python -m eval.research_demand --out <same path>` first")
        clusters = queue["clusters"]
        index = int(self.config.get("cluster_index", 0)) % max(1, len(clusters))
        cluster = dict(clusters[index])
        # RESEARCH IS EXCLUDED FROM THE DECLARED PANEL; the evaluator is not. A cluster whose
        # functions are the same functions the panel will score would make the intervention and its
        # test set the same set, and the transfer comparison would be measuring memorisation. The
        # exclusion belongs on THIS side -- the researcher may not be given the panel -- and not on
        # the evaluator, which must execute every task the freeze declared.
        spec = self.declared_spec() or {}
        panel = set(spec.get("expected_task_ids") or [])
        overlap = sorted(panel & set(cluster.get("functions") or []))
        if overlap:
            raise SystemExit(
                f"the cluster at index {index} covers {len(overlap)} function(s) of the declared "
                f"evaluation panel ({overlap[:5]}); research is excluded from the panel, so this "
                f"cluster cannot motivate the intervention that the panel scores")
        # One redacted example: the researcher needs to see WHAT failed to form a hypothesis, and the
        # example is a synthetic probe shape, not a held-out function. The demand queue already
        # excluded held-out names.
        cluster["focus_example"] = ("u32 syn_probe(u32 x) { return *(u32 *)(x + {{K}}); }")
        cluster["panel_excluded"] = len(panel)
        return cluster

    def stage_verify(self) -> dict:
        """Read the worker's notebook and keep only rows its own verdict confirmed."""
        research = self.receipt("research") or {}
        notebook = Path(research.get("notebook", ""))
        rows = [json.loads(line) for line in notebook.read_text("utf-8").splitlines() if line.strip()] \
            if notebook.exists() else []
        confirmed = [row for row in rows if row.get("status") == "synthetic_confirmed"]
        findings = []
        for row in confirmed:
            proposal = row.get("proposal") or {}
            findings.append({
                "id": row["id"], "experiment_id": row["id"],
                "title": proposal.get("title", ""), "rationale": proposal.get("rationale", ""),
                "metric": proposal.get("metric"), "relation": proposal.get("relation"),
                "measurements": row.get("measurements"),
                "status": "confirmed", "source": "local-research-notebook",
                "provenance": {"recipe_id": row.get("recipe_id"),
                               "model_digest": row.get("model_digest")},
            })
        finding = findings[-1] if findings else None
        self.state["evidence_status"] = "confirmed" if finding else "refuted"
        self.publish()
        return {"rows": len(rows), "confirmed": len(confirmed),
                "finding": finding, "findings": len(findings)}

    def stage_assemble(self) -> dict:
        """Turn the finding into a PROPOSED memory note. Never a behaviour change by itself."""
        from eval.rsi_interventions import (Note, confirm_applicability, intervention_from_finding,
                                            load, save)
        verify = self.receipt("verify") or {}
        finding = verify.get("finding")
        cluster = (self.receipt("research") or {}).get("cluster") or {}
        notes = [item for item in load(self.root / "notes.jsonl") if isinstance(item, Note)]
        proposed = None
        if finding:
            # The applicability block is what makes this a bounded intervention rather than a global
            # prompt change: the note is retrieved only for states carrying the same failure signature
            # that motivated the research.
            applicability = {"error_class": cluster.get("error_class"),
                             "residual_kind": cluster.get("residual_kind")}
            text = (f"measured: {finding['title']}. {finding['rationale']} "
                    f"(metric {finding['metric']} is {finding['relation']} in the changed form; "
                    f"confirmed on three parameter values by experiment {finding['experiment_id']})")
            proposed = intervention_from_finding(finding, kind="memory", text=text,
                                                 applicability=applicability)
            notes.append(proposed)
        summary = save(self.root / "notes.jsonl", notes)
        # THE CONFIRMATION STEP, which did not exist. Without it every note stayed `proposed`, no child
        # was ever frozen, and the paired comparison ran with zero notes enabled -- a guaranteed null
        # that I nearly reported as a capability result. See `confirm_applicability` for exactly what
        # the test does and does not establish.
        confirmation = None
        if proposed:
            confirmation = confirm_applicability(proposed, cluster)
            save(self.root / "notes.jsonl", notes)
        return {"proposed": proposed.as_dict() if proposed else None,
                "confirmation": confirmation,
                "notes": len(notes), "notes_file": save(self.root / "notes.jsonl", notes),
                "status": proposed.status if proposed else "no-confirmed-finding"}

    def stage_candidate_frozen(self) -> dict:
        """Freeze this round's candidate = its parent + the confirmed notes.

        Weights are unchanged, and the manifest says so. THE IDS COME FROM THE ROUND: round 1 freezes
        S1 from S0, round 2 freezes S2 from S1. Writing "S1" and "S0" as literals here is what made a
        two-round experiment produce one generation twice.
        """
        from eval import generation_manifest as gm
        from eval.rsi_interventions import Note, load
        notes = [item for item in load(self.root / "notes.jsonl") if isinstance(item, Note)]
        confirmed = [note for note in notes if note.status == "confirmed"]
        if not confirmed:
            # NAMING THE REASON MATTERS: this branch used to say "no confirmed note" for a pipeline in
            # which NO note could ever be confirmed, which reads like a research outcome and was a
            # missing function.
            return {"generation": None,
                    "reason": "no note passed the applicability test; nothing to freeze as candidate",
                    "notes_seen": len(notes), "candidate": self.candidate_id}
        parent = gm.load(self.root / "generations", self.parent_id)
        child = gm.Generation(**{**parent.as_dict(), "id": self.candidate_id,
                                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "parent": self.parent_id,
                                 "memory": {**gm.hash_artifact(self.root / "notes.jsonl"),
                                            "confirmed_notes": len(confirmed)},
                                 "proposals": [note.provenance.get("finding_id", "")
                                               for note in confirmed],
                                 "notes": "memory-only change: base weights and adapter are "
                                          "identical to the parent generation; only the verified-memory "
                                          "snapshot differs"})
        path = gm.freeze(self.root / "generations", child)
        return {"generation": self.candidate_id, "manifest": str(path),
                "fingerprint": child.content_fingerprint(), "confirmed_notes": len(confirmed)}

    def stage_evaluate(self) -> dict:
        """The paired transfer comparison over the DECLARED panel. Evaluation is reserved first."""
        from eval.rsi_interventions import Note, load
        from eval.rsi_transfer import eligible_panel, paired_transfer

        spec = self.declared_spec()
        if not spec:
            raise Stopped("this round has no frozen evaluation specification; the frozen stage "
                          "declares the panel and it must exist before anything can be measured "
                          "against it")
        declared = list(spec["expected_task_ids"])
        motivating = set((self.receipt("research") or {}).get("cluster", {}).get("functions") or [])
        # THE EVALUATOR KEEPS ITS RIGHT TO EXECUTE THE FROZEN SPLIT. `purpose="evaluation"` applies
        # the motivating exclusion, which protects the measurement, and does NOT apply the held-out
        # exclusion, which exists to keep held-out functions away from RESEARCH. Applying the
        # research-side exclusion here made an isolated evaluator refuse to run the very tasks it
        # exists to execute: measured, `eligible_panel(["t1","t2"], held_out={"t1","t2"})` kept 0.
        panel, panel_report = eligible_panel(declared, motivating=motivating, held_out=set(),
                                            purpose="evaluation")
        if len(panel) != len(declared):
            # THE PANEL DOES NOT SHRINK. A declared task that cannot be measured makes the
            # evaluation incomplete, which the gate reports; it never becomes a smaller panel.
            self.event("declared_panel_incomplete", declared=len(declared), runnable=len(panel),
                       dropped=panel_report.get("excluded_motivating"))
        if not panel:
            raise Stopped("no eligible transfer functions after exclusions")

        notes = [note for note in load(self.root / "notes.jsonl") if isinstance(note, Note)
                 and note.status == "confirmed"]
        per_arm = len(panel) * int(self.config["caps"]["per_task_actions"])
        self.ledger.reserve_evaluation("evaluate", calls=2 * per_arm, compiles=2 * per_arm)

        def baseline_factory():
            # The arm that must be identical to the intervention arm in every other respect: same
            # weights, same adapter, same renderer, same retrieval wrapper -- with an EMPTY note set, so
            # the wrapper does the same work and retrieves nothing.
            return self._policy(notes=[])

        def intervention_factory():
            return self._policy(notes=notes)

        payload = paired_transfer(
            panel=panel, budget=int(self.config["caps"]["per_task_actions"]),
            repo=Path(self.config["panel"].get("repo", Path.home() / "decomp/sbk1")),
            kb=Path(self.config["panel"].get("kb", Path.home() / "decomp/kb-sbk1.sqlite")),
            baseline_factory=baseline_factory, intervention_factory=intervention_factory,
            ledger=self.ledger, stage="evaluate")
        payload["panel_report"] = panel_report
        # WHAT WAS DECLARED, WHAT RAN, AND WHAT FAILED, all three, because the gate's coverage check
        # is against the declared panel and an infrastructure error must not read as a completed draw.
        payload["declared_panel"] = declared
        payload["spec"] = spec
        payload["infrastructure_errors"] = [
            {"arm": arm, "function": row.get("function"), "error": row.get("error")}
            for arm in ("baseline", "intervention")
            for row in (payload.get("rows") or {}).get(arm, []) if row.get("error")]
        payload["notes_enabled"] = len(notes)
        # WHETHER THE INTERVENTION WAS ACTUALLY ACTIVE, recorded rather than assumed. The previous run
        # reported a 0-vs-0 delta from two arms that could not differ; a retrieval count is what makes
        # that failure impossible to repeat silently.
        payload["retrieval"] = {
            "notes_available": len(notes),
            # Read from the arm's retrieval log, not from a field the grader never sets (the first
            # version of this counter looked for `note_retrieved` on grading decisions, which do not
            # carry it, so it would have reported zero for an intervention that fired on every call).
            "decisions_with_a_note_retrieved": sum(
                1 for row in payload["rows"]["intervention"]
                for entry in row.get("retrieval", []) if entry.get("retrieved")),
            "notes_retrieved": sorted({
                note_id for row in payload["rows"]["intervention"]
                for entry in row.get("retrieval", []) for note_id in entry.get("retrieved", [])}),
        }
        return payload

    def _policy(self, *, notes: list):
        arm = self.config["downstream"]["arm"]
        if arm == "scripted":
            from eval.tool_agent import ScriptedPolicy
            return ScriptedPolicy()
        from eval.tool_action_eval import load_adapter_policy
        from eval.rsi_interventions import NoteRetrievalPolicy
        policy = load_adapter_policy(self.config["downstream"]["base"],
                                     self.config["downstream"]["adapter"])
        # State-scoped retrieval, not a global prompt change: the wrapper decides per decision which
        # notes are compatible with the state actually in front of the policy.
        wrapper = NoteRetrievalPolicy(policy, list(notes))
        wrapper.name = "model-with-notes" if notes else "model-no-notes"
        return wrapper

    def stage_decide(self) -> dict:
        """The preregistered gate -- the PROJECT's gate, not one invented here.

        `eval.posttraining_gate.decide` already implements the acceptance rule this repository trusts:
        R1 closes at least one task the baseline did not, R2 loses none, R3 the panel is at least
        MIN_TASKS_FOR_A_VERDICT, R4 both arms ran every declared task, R5 the declared draws happened.
        Reusing it means a smoke-round panel is ruled INADEQUATE BY THE EXISTING RULE rather than by a
        threshold this file chose after seeing its own numbers, and no secondary measure can promote a
        generation: the gate never reads the procedure metrics at all.

        THE SPECIFICATION COMES FROM THE FREEZE, NOT FROM THIS RUN. `expected_task_ids` used to be
        `evaluation["usable"]` -- the tasks whose SETUP succeeded -- which is the run describing what
        it managed to do and then being graded against its own description. Measured on that version:
        13 tasks declared, 12 set up, one baseline row carrying an infrastructure error, and the
        verdict was `promote`. The specification is read from the `frozen` receipt written before any
        setup ran, and an infrastructure error is reported to the gate as a row with NO DRAW COUNT,
        which the existing R5 already fails.
        """
        evaluation = self.receipt("evaluate") or {}
        production = self.production_matches()
        try:
            from eval.posttraining_gate import EvaluationSpec, decide

            def rows_for(arm: str) -> dict:
                return _gate_rows((evaluation.get("rows") or {}).get(arm, []))

            frozen = self.declared_spec()
            # THE FAILED ROWS ARE READ FROM THE ROWS THEMSELVES, not from a second field that a
            # caller could forget to write: the verdict and the receipt then describe the same data.
            infra_errors = [{"arm": arm, "function": row.get("function"),
                             "error": str(row.get("error"))[:300]}
                            for arm in ("baseline", "intervention")
                            for row in (evaluation.get("rows") or {}).get(arm, [])
                            if row and row.get("error")] or list(
                                evaluation.get("infrastructure_errors") or [])
            if not frozen:
                # NO SPECIFICATION, NO VERDICT. An undeclared panel is exactly the state in which an
                # incomplete run looks complete, so the gate is handed an empty specification and
                # returns `ineligible` with the reason on the record.
                spec = EvaluationSpec()
            else:
                spec = EvaluationSpec(
                    expected_task_ids=tuple(frozen["expected_task_ids"]),
                    split=frozen.get("split", self.config["panel"]["split"]),
                    draws_per_task=int(frozen.get("draws_per_task", 0)),
                    kind=frozen.get("kind", self.config["gate"].get("kind", "frozen")),
                    manifest_sha256=str(frozen.get("manifest_sha256", "")))
            outcome = decide(baseline=rows_for("baseline"), adapter=rows_for("intervention"),
                             spec=spec,
                             min_tasks=int((frozen or {}).get(
                                 "min_tasks", self.config["gate"].get("min_tasks", 12))))
            decision = {"promote": "accept", "keep-baseline": "keep-parent",
                        "inconclusive": "inconclusive", "ineligible": "inconclusive"}[outcome.verdict]
            return {"decision": decision, "verdict": outcome.verdict, "reasons": outcome.reasons,
                    "conditions": outcome.conditions, "counts": outcome.counts,
                    "spec": spec.as_dict(), "spec_source": ("frozen-receipt" if frozen else
                                                            "absent-no-declared-panel"),
                    "production_matches": production,
                    "declared_panel": len(evaluation.get("declared_panel") or []),
                    "usable": len(evaluation.get("usable") or []),
                    "infrastructure_errors": infra_errors,
                    "secondary_only": True,
                    "delta": evaluation.get("delta")}
        except Exception as exc:                                # noqa: BLE001
            return {"decision": "inconclusive", "verdict": "gate-unavailable",
                    "reasons": [f"{type(exc).__name__}: {exc}"], "production_matches": production,
                    "delta": evaluation.get("delta")}

    def production_matches(self) -> dict:
        """The ratchet is READ, never written, by this experiment."""
        kb = Path(self.config["panel"].get("kb", Path.home() / "decomp/kb-sbk1.sqlite"))
        if not kb.exists():
            return {"available": False}
        conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
        try:
            exact = conn.execute("select count(distinct func_addr) from attempts "
                                 "where coalesce(exact,0)=1").fetchone()[0]
        finally:
            conn.close()
        return {"available": True, "functions_with_an_exact_attempt": exact}

    # --- driver -------------------------------------------------------------

    def run_round(self) -> None:
        """One pass over the stages, for the round named in the state."""
        for stage in STAGES:
            self.check_stop()
            existing = self.receipt(stage)
            if existing:
                self.event("stage_skipped_resumed", stage=stage, round=self.round)
                self.set_stage(stage)
                if stage == "decide":
                    # A RESUMED ROUND REPORTS THE VERDICT IT ALREADY REACHED. Without this the last
                    # skipped stage left the state at `decide` with no decision, so a resumed run
                    # returned `stage: decide` where the original returned `accept` -- the same run
                    # described two different ways, and the round pointer unable to advance.
                    decision = existing.get("decision")
                    self.state.update(
                        gate_reason="; ".join(existing.get("reasons") or [])[:600],
                        gate_verdict=existing.get("verdict"),
                        generation=self.parent_id,
                        candidate=self.candidate_id if decision == "accept" else None,
                        stage="done" if decision != "accept" else "accept")
                continue
            self.set_stage(stage)
            payload = getattr(self, f"stage_{stage.replace('-', '_')}")()
            self.record(stage, payload)
            if stage == "decide":
                decision = payload["decision"]
                self.state.update(gate_reason="; ".join(payload.get("reasons") or [])[:600],
                                  gate_verdict=payload.get("verdict"),
                                  generation=self.parent_id,
                                  candidate=self.candidate_id if decision == "accept" else None)
                self.state["stage"] = "done" if decision != "accept" else "accept"
            self.publish()

    def advance_round(self, *, more_rounds: bool = True) -> None:
        """Move the generation pointer after an ACCEPTED round.

        THE PARENT POINTER MOVES HERE AND ONLY HERE. `state["parent"]` was initialised to None and
        never written, so even a run that accepted a child could not say what it had been derived
        from, and the next round started from S0 again.

        The pointer moves on the LAST accepted round too: the accepted candidate is the active
        generation whether or not another round is declared to follow it, and the state says which
        generation it was derived from.
        """
        # BOTH IDS ARE READ BEFORE THE POINTER MOVES. `self.parent_id` is derived from
        # `state["round"]`, so reading it after the update names the NEXT round's parent and the
        # receipt said "S2 accepted from S2".
        accepted, derived_from = self.candidate_id, self.parent_id
        self.state.update(parent=derived_from, generation=accepted, candidate=None,
                          round=self.round + 1)
        self.state["gate_reason"] = (
            f"{accepted} accepted from {derived_from}"
            + ("" if more_rounds else "; no further round was declared"))
        self.state["stage"] = "frozen" if more_rounds else "accept"
        self.event("round_advanced", accepted=accepted, parent=derived_from, round=self.round,
                   more_rounds=more_rounds)
        self.publish()

    def run(self) -> dict:
        """Run up to the declared number of rounds, stopping at the first non-accepted one.

        `rounds` WAS IGNORED: `run()` walked the stages once and returned, so a run configured for two
        generations produced one, and because the receipts were not round-scoped a second call
        reported the FIRST round's verdict as its own result. A round advances only on an accepted
        gain -- a round that kept the parent has nothing to build a next generation from, and
        starting one anyway would spend research budget to re-derive the same intervention.
        """
        try:
            while self.round <= self.rounds_declared:
                self.run_round()
                if self.state.get("stage") != "accept":
                    if self.round < self.rounds_declared:
                        self.event("rounds_not_reached", declared=self.rounds_declared,
                                   stopped_after=self.round,
                                   because=self.state.get("gate_verdict") or "not accepted")
                    break
                more = self.round < self.rounds_declared
                self.advance_round(more_rounds=more)
                if not more:
                    break
        except Stopped as exc:
            self.state.update(stage="stopped", gate_reason=str(exc))
            self.event("stopped", reason=str(exc))
        except Exception as exc:                                # noqa: BLE001
            self.state.update(stage="failed", gate_reason=f"{type(exc).__name__}: {exc}")
            self.event("failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            self.publish()
            _write(self.root / "receipt.json", self.state)
        return self.state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT / "eval/results/narrow-rsi-20260921")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--stage", default=None, help="run one stage and stop (debugging)")
    args = ap.parse_args(argv)
    config = _read(args.config, {}) or {}
    experiment = Experiment(args.root, config)
    if args.stage:
        payload = getattr(experiment, f"stage_{args.stage.replace('-', '_')}")()
        experiment.record(args.stage, payload)
        print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, indent=2, default=str))
        return 0
    state = experiment.run()
    print(json.dumps({k: state[k] for k in ("experiment_id", "stage", "generation", "candidate",
                                            "evidence_status", "gate_reason") if k in state},
                     indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
