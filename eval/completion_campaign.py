"""Resumable campaign with legacy DAG or evidence-directed repair scheduling.

Example: python -m eval.completion_campaign --repo ... --db ... --state ...
  --functions f g --max-work-items 12 --model-calls 2

Resume with the same arguments and --resume. A work budget ending is a PAUSE,
not completion. Exhausting strategies is a stall, not proof of impossibility.
This is a header-assisted development campaign, not an unseen benchmark.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time

from eval import agentrepair, callgraph, frozen_wavefront, integration_gate, prepare_integration
from miner import units
from solver import compile_recovery, evidence_schedule, llm, m2c_context, modelrepair, project_headers, residual, sdk_intake, workspace
from solver.target_intake import AssemblyBackendRequired
from solver import repair_queue


PROFILES = (
    {"name": "local_rewrites", "deterministic_budget": 32, "deterministic_depth": 2, "model": False},
    {"name": "schema_patch", "deterministic_budget": 0, "think": "low", "model": True,
     "brief": "Use the source-bound instruction/byte residual to choose one bounded patch. "
              "Explain the source cause; return an executable edit, not a long diagnosis."},
    {"name": "deeper_composition", "deterministic_budget": 128, "deterministic_depth": 5, "model": False},
    {"name": "reasoned_alternative", "deterministic_budget": 0, "think": "high", "model": True,
     "brief": "Earlier bounded patching/composition did not finish this source. Test a different "
              "source-shape hypothesis against the exact instructions. Consider control structure, "
              "value lifetimes and ABI types; do not presume register differences are semantic errors."},
    {"name": "typed_transaction", "deterministic_budget": 0, "think": "low", "model": True,
     "type_transaction": True,
     "brief": "Resolve one connected type graph using atomic source-slot edits. Keep the public ABI, "
              "introduce typed locals, update dependent members and byte-copy uses together. "
              "Use target return dataflow rather than inventing return values to silence diagnostics."},
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@contextmanager
def campaign_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def next_profile(node: dict, model_calls: int) -> dict | None:
    if node["status"] in {"object_exact", "integrated", "parked", "function_exact_pending_integration"}:
        return None
    if not node.get("source_sha256"):
        return {"name": "intake", "model": False}
    used = {(job["source_sha256"], job["profile"]) for job in node.get("jobs", [])}
    # These adapters need source/diagnostics, not an existing object or an LLM.
    # Keep their visit separate from instruction rewrites and model budgets.
    failed_compile = node.get('residual', {}).get('compiled') is False
    failed_frontend = (node.get('residual', {}).get('frontend') or {}).get('passed') is False
    consecutive_recovery = 0
    for job in reversed(node.get('jobs', [])):
        if job['profile'] != 'compile_recovery':
            break
        consecutive_recovery += 1
    # Header-context variants can keep changing the source hash without fixing
    # its diagnostics. Allow a follow-up recovery, then yield to other profiles.
    if ((failed_compile or failed_frontend) and consecutive_recovery < 2
            and (node['source_sha256'], 'compile_recovery') not in used):
        return {'name': 'compile_recovery', 'model': False, 'deterministic_budget': 0,
                'brief': 'Run existing build-context, type-constraint and C89 recovery before model repair.'}
    profiles = PROFILES
    if (node.get('residual',{}).get('compiled')
            and (node.get('residual',{}).get('frontend') or {}).get('passed') is True
            and not node.get('semantic_validation')):
        if (node['source_sha256'],'semantic_handoff') not in used:
            return {'name':'semantic_handoff','model':bool(model_calls),'think':'low','deterministic_budget':0,
                    'brief':'Repair the reported differential counterexample before byte polishing.'}
    diagnostics = (node.get('residual', {}).get('frontend') or {}).get('diagnostics', '')
    if node.get('residual', {}).get('compiled') is False and diagnostics.count("member reference base type 'void'") >= 2:
        profiles = tuple(sorted(PROFILES, key=lambda p: not p.get('type_transaction', False)))
    for profile in profiles:
        if profile.get('type_transaction') and node.get('residual', {}).get('compiled') is not False:
            continue
        if not profile["model"] and node.get("residual", {}).get("compiled") is False:
            continue  # instruction rewrites require a compiled object
        if not profile["model"] and (node.get("residual", {}).get("frontend") or {}).get("passed") is False:
            continue
        if profile["model"] and not model_calls:
            continue
        if (node["source_sha256"], profile["name"]) not in used:
            return profile
    return None


def scheduled_profile(state, node):
    scheduler = state['config'].get('scheduler', 'legacy')
    if scheduler == 'investigation-v1':
        from solver.investigation import profile
        return profile(node, state['config']['model_calls'], PROFILES)
    if scheduler == 'evidence-v1':
        return repair_queue.next_profile(node, state['config']['model_calls'], PROFILES)
    return next_profile(node, state['config']['model_calls'])


def translation_units(conn) -> dict:
    """Function -> layout-derived unit label, for scheduling locality only.

    Clustering comes from recorded function ranges (miner/units.py), NOT
    from the reference build's linker map: `functions.tu_id` is the finished
    decomp's own file assignment and is ground truth for checking, never an
    input. These ranges can be assisted ELF metadata; gaps are heuristic
    cluster boundaries, not proof of original file identity. An older KB
    without range columns yields no index. Other database failures propagate.
    """
    columns = {row[1] for row in conn.execute('PRAGMA table_info(functions)')}
    if not {'addr', 'size', 'name'} <= columns:
        return {}
    rows = conn.execute("SELECT addr,size,name FROM functions").fetchall()
    return units.clusters([(addr, size, name) for addr, size, name in rows])


def reference_units(conn) -> dict:
    """Linker-map unit assignment. ORACLE ONLY -- never feed this to a run."""
    try:
        rows = conn.execute(
            "SELECT f.name, COALESCE(t.name, 'tu:' || f.tu_id) FROM functions f "
            "LEFT JOIN tus t ON t.id = f.tu_id WHERE f.tu_id IS NOT NULL").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {name: unit for name, unit in rows}


def choose(state: dict) -> tuple[str, dict] | None:
    if state['config'].get('scheduler') in {'evidence-v1', 'investigation-v1'}:
        _, selected = repair_queue.project(state, PROFILES)
        return selected
    eligible = [(len(node["jobs"]), node["dag_level"], node["instruction_count"], name, profile)
                for name, node in state["nodes"].items()
                if not state["config"].get("compile_sweep") or node.get("residual", {}).get("compiled") is not True
                    or (node.get("residual", {}).get("frontend") or {}).get("passed") is False
                if (profile := next_profile(node, state["config"]["model_calls"])) is not None]
    if not eligible:
        return None
    _, _, _, name, profile = min(eligible, key=lambda row: row[:4])
    return name, profile


def status(state: dict) -> str:
    nodes = list(state["nodes"].values())
    if state["config"].get("compile_sweep") and not choose(state):
        failed = [n for n in nodes if n.get('residual', {}).get('compiled') is False
                  or (n.get('residual', {}).get('frontend') is not None
                      and n['residual']['frontend'].get('passed') is not True)]
        return "compile_sweep_stalled" if failed else "compile_sweep_complete"
    if nodes and all(n["status"] == "integrated" for n in nodes):
        return "cohort_integrated"
    if nodes and all(n["status"] in {"object_exact", "integrated"} for n in nodes):
        return "cohort_objects_exact"
    if not choose(state) and any(n["status"] == "function_exact_pending_integration" for n in nodes):
        return "awaiting_integration"
    return "paused_budget" if choose(state) else "stalled_requires_new_strategy_or_evidence"


def accept(node: dict, profile: dict, result: dict, receipt: Path):
    before = node.get("source_sha256", "")
    node["jobs"].append({"profile": profile["name"], "source_sha256": before,
                          "receipt": str(receipt), "status": result.get("status", "evaluated")})
    if 'evidence_key' in profile:
        node['jobs'][-1].update(evidence_key=profile['evidence_key'], lane=profile['lane'],
                                model=profile['model'])
    if result.get('auxiliary'):
        node.setdefault('capability_experiments', []).append({'receipt': str(receipt),
            'status': result.get('status'), 'issue_key': profile.get('issue_key')})
        return
    if result.get("status") == "parked":
        node.update(status="parked", blocker=result.get("blocker"))
        return
    node.update({key: result[key] for key in (
        "attempt_id", "source", "source_sha256", "score", "verification", "frontier", "residual")
        if key in result})
    node["status"] = "object_exact" if result.get("exact") else "pending"
    if not result.get("exact") and (result.get("verification") or {}).get("function_boundary", {}).get("function_exact"):
        node["status"] = "function_exact_pending_integration"
    frontend = result.get("residual", {}).get("frontend")
    if frontend is not None and frontend.get("passed") is not True:
        node["status"] = "pending"
        if frontend.get("status") == "unavailable":
            node.update(status="parked", blocker={"status": "frontend_unavailable",
                                                "diagnostics": frontend.get("diagnostics")})
    node["last_outcome"] = {key: result.get(key) for key in (
        "score", "calls_attempted", "incomplete_responses", "invalid_proposals", "best_score_improved")}
    node["last_outcome"]["diagnostics"] = result.get("log", [])[-4:]
    node['semantic_validation'] = result.get('semantic_validation')
    if result.get('champions'): node['champions'] = result['champions']


def _intake(*, repo: Path, db: Path, function: str, node: dict, out: Path) -> dict:
    try:
        ws = workspace.bootstrap(repo, function)
    except AssemblyBackendRequired:
        try:
            ws = sdk_intake.bootstrap(repo, db, function)
        except sdk_intake.UnsupportedTarget as exc:
            return {"status": "parked", "blocker": exc.report}
    with sqlite3.connect(db, timeout=120) as conn:
        parent = node.get("seed_attempt_id")
        if parent is not None:
            source = agentrepair._source_for_attempt(conn, parent, function)
            variants, context = [("explicit-historical-seed", source)], []
        else:
            source = (ws / "base.c").read_text() if (ws / "base.c").exists() else ""
            variants, context = m2c_context.seed_variants(
                repo, function, ws / "target.s", workspace.target_asm(ws, function), source)
            variants = ([("assembly-only-m2c", source)] if source else []) + variants
        origins = {candidate: parent for _, candidate in variants}
        for item in node.get("seed_frontier", [])[:3]:
            retained_id = int(item["attempt_id"])
            retained = agentrepair._source_for_attempt(conn, retained_id, function)
            if hashlib.sha256(retained.encode()).hexdigest() != item["source_sha256"]:
                raise ValueError("forked frontier source identity changed")
            if retained not in origins:
                variants.append(("forked-intake-context", retained))
                origins[retained] = retained_id
        # Apply the existing compiler-context adapters to context-generated
        # drafts too. A successful m2c run can still emit redundant externs or
        # omit headers for globals/callback values. Keep the original branch.
        expanded, seen = [], set()
        for label, candidate in variants:
            for adapted_label, adapted in [(label, candidate)] + project_headers.preflight_variants(
                    repo, function, workspace.target_asm(ws, function), candidate):
                if adapted not in seen:
                    expanded.append((adapted_label, adapted, origins[candidate]))
                    seen.add(adapted)
        variants = expanded
        if not variants:
            return {"status": "parked", "blocker": {"status": "no_initial_candidate", "context": context}}
        scored = []
        for index, (label, candidate, candidate_parent) in enumerate(variants):
            tag = f"{function}_campaign_intake_{time.time_ns()}_{index}"
            att = workspace.score(ws, repo, tag, candidate, conn=conn, func=function,
                strategy="campaign-intake:" + label, parent_attempt_id=candidate_parent,
                run_id=out.stem, relation="campaign-intake", action=label)
            scored.append((att, candidate, ws / (tag + ".o")))
            if not att.compiled and "contains a do-while loop" in att.compiler_stderr:
                # Reuse the existing sanctioned for/break lowering, including
                # its refusal of unsafe continue semantics. Keep both receipts.
                from tools.score_repo_function import rewrite_do_while
                try:
                    lowered = rewrite_do_while(candidate)
                except ValueError as exc:
                    context.append({"stage": "do-while-lowering", "status": "declined", "reason": str(exc)})
                else:
                    if lowered != candidate:
                        fixed_tag = tag + "_for_break"
                        fixed = workspace.score(ws, repo, fixed_tag, lowered, conn=conn, func=function,
                            strategy="campaign-intake:do-while-for-break", parent_attempt_id=att.receipt_id,
                            run_id=out.stem, relation="compile-repair", action="existing do-while lowering")
                        scored.append((fixed, lowered, ws / (fixed_tag + ".o")))
                        if workspace.repair_complete(fixed) and integration_ready(lowered, function):
                            break
            if workspace.repair_complete(att) and integration_ready(candidate, function):
                break
        if not any(row[0].compiled for row in scored):
            parents = modelrepair._frontier([modelrepair.CandidateState(code, attempt, obj)
                for attempt, code, obj in scored], 2)
            recovered = set(code for _, code, _ in scored)
            for state in parents:
                try:
                    children, reports = compile_recovery.variants(conn, repo, function, ws,
                        state.source, state.attempt)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    children, reports = [], [{'stage': 'compile-recovery', 'status': 'error', 'reason': str(exc)}]
                context.append({'parent_attempt_id': state.attempt.receipt_id, 'recovery': reports})
                for label, code in children:
                    if code in recovered:
                        continue
                    recovered.add(code)
                    tag = f"{function}_compile_recovery_{time.time_ns()}"
                    attempt = workspace.score(ws, repo, tag, code, conn=conn, func=function,
                        strategy='campaign-compile-recovery:'+label, parent_attempt_id=state.attempt.receipt_id,
                        run_id=out.stem, relation='compile-recovery', action=label,
                        extra={'compile_recovery': reports})
                    scored.append((attempt, code, ws/(tag+'.o')))
        att, source, object_path = max(scored, key=lambda row: (row[0].exact,
            row[0].exact and integration_ready(row[1], function),
            workspace.repair_complete(row[0]),
            (row[0].verification or {}).get("function_boundary", {}).get("function_exact", False),
            row[0].compiled, row[0].score, tuple(-v for v in modelrepair.compile_error_rank(row[0])),
            tuple(-v for v in modelrepair.context_rank(row[1]))))
        # A standalone pass obtained with extra declarations must not preempt
        # repair of an equally object-exact draft using real project headers.
        # Those receipts remain in the DB, but cannot terminate this frontier.
        alternatives = modelrepair._frontier([modelrepair.CandidateState(code, attempt, obj,
            ("retained intake context",), ("intake-context",)) for attempt, code, obj in scored
            if not (att.exact and integration_ready(source, function) and attempt.exact
                    and not integration_ready(code, function))], 3)
    source_path = out.with_suffix(".best.c")
    source_path.write_text(source)
    return {"status": "evaluated", "exact": workspace.repair_complete(att), "attempt_id": att.receipt_id,
            "source": str(source_path), "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "score": att.score, "verification": att.verification, "frontier": [
                {"attempt_id": state.attempt.receipt_id, "source_sha256": hashlib.sha256(state.source.encode()).hexdigest(),
                 "score": state.attempt.score, "compiled": state.attempt.compiled,
                 "hypotheses": list(state.labels), "kinds": list(state.kinds)} for state in alternatives], "context": context,
            "residual": residual.build(att, target_asm=workspace.target_asm(ws, function),
                target_object=ws / "target.o", candidate_object=object_path if att.compiled else None).to_dict()}


def integration_ready(source: str, function: str) -> bool:
    try:
        prepare_integration.candidate_parts(source, function)
        return True
    except ValueError:
        return False


def preflight_integration(*, repo: Path, db: Path, entries: list[dict], artifacts: Path,
                          tag: str) -> tuple[list[dict], list[dict]]:
    """An unsupported candidate must not block unrelated exact replacements."""
    eligible, blocked = [], []
    for index, item in enumerate(entries):
        try:
            prepare_integration.prepare(repo=repo, db=db, entries=[item],
                output_dir=artifacts / f"{tag}-preflight-{index}")
            eligible.append(item)
        except (OSError, ValueError) as exc:
            blocked.append({"status": "preparation_blocked", "functions": [item["function"]], "error": str(exc)})
    return eligible, blocked


def integrate_candidates(*, repo: Path, db: Path, entries: list[dict], artifacts: Path) -> tuple[list[dict], list[dict]]:
    """Isolate failed integration batches, then verify the surviving union.

    Individual successes are diagnostic until the combined survivor build
    passes. Operational errors or an explicit STOP never trigger bisection.
    All replacements stay inside integration_gate's disposable game copies.
    """
    records = []
    def build(batch):
        tag = str(time.time_ns())
        manifest = prepare_integration.prepare(repo=repo, db=db, entries=batch,
            output_dir=artifacts / (tag + "-prepared"))
        receipt_path = artifacts / (tag + "-integration.json")
        receipt = integration_gate.run(repo=repo, manifest=manifest, output=receipt_path)
        records.append({"receipt": str(receipt_path), "status": receipt["status"],
                        "functions": [e["function"] for e in batch]})
        log = Path(receipt["build_log"]).read_text(errors="replace") if receipt.get("build_log") else ""
        if re.search(r"(?i)\b(?:STOP|do not (?:retry|continue)|must stop)\b", log):
            raise RuntimeError("integration build requested STOP; no further attempts")
        return receipt
    def isolate(batch):
        receipt = build(batch)
        if receipt.get("whole_rom_verified"):
            return batch
        if receipt["status"] not in {"build_failed", "rom_mismatch"}:
            raise RuntimeError("integration operational failure: " + receipt["status"])
        if len(batch) == 1:
            return []
        midpoint = len(batch) // 2
        return isolate(batch[:midpoint]) + isolate(batch[midpoint:])
    try:
        survivors = isolate(entries)
        if survivors and survivors != entries:
            # A union can introduce cross-TU effects absent in subset tests.
            if not build(survivors).get("whole_rom_verified"):
                return [], records
        return survivors, records
    except (OSError, ValueError, RuntimeError) as exc:
        records.append({"status": "integration_halted", "error": str(exc)})
        return [], records


def retained_candidates(node):
    """Keep both lanes ahead of incidental beam alternatives across visits."""
    champions = node.get('champions') or {}
    ordered = [champions.get('semantic'), champions.get('byte'), *node.get('frontier',[])]
    rows, seen = [], {node.get('source_sha256')}
    for row in ordered:
        if row and row.get('source_sha256') not in seen:
            rows.append(row)
            seen.add(row['source_sha256'])
    return rows[:3]


def execute(*, repo: Path, db: Path, function: str, node: dict, profile: dict,
            config: dict, out: Path) -> dict:
    if profile.get('capability_task'):
        from eval import capability_repair
        result = capability_repair.run(Path(config['project']), profile['capability_task'],
            out.with_suffix('.engineering'), model=config['model'], endpoint=config['endpoint'],
            calls=min(8, config['model_calls']), timeout=config['timeout'])
        return {'auxiliary': True, 'status': result['status'],
                'report': str(out.with_suffix('.engineering') / 'report.json')}
    if profile["name"] == "intake":
        return _intake(repo=repo, db=db, function=function, node=node, out=out)
    source = Path(node["source"]).read_text()
    if hashlib.sha256(source.encode()).hexdigest() != node["source_sha256"]:
        raise ValueError("campaign candidate changed outside controller")
    from eval import campaign_data
    result = agentrepair.run(repo=repo, db=db, function=function, source=source,
        source_parent_attempt_id=node["attempt_id"], out=out.with_suffix(".repair.json"),
        best_source_out=out.with_suffix(".best.c"), model=config["model"], endpoint=config["endpoint"],
        draws=1, depth=max(4, config['model_calls']) if node.get('residual', {}).get('compiled') is False else 4,
        beam=3, max_calls=config["model_calls"] if profile["model"] else 0,
        timeout=config["timeout"], think=profile.get("think", "low"), num_thread=12,
        temperature=0.35, num_predict=config["num_predict"], seed=20260904,
        cache_dir=None, verbose=False, retained_frontier=tuple(retained_candidates(node)),
        deterministic_budget=profile["deterministic_budget"],
        deterministic_depth=profile.get("deterministic_depth", 2), structured_output=True,
        retry_invalid=True, include_header_context=True,
        compile_only=config.get('compile_sweep', False),
        type_transaction=profile.get('type_transaction', False),
        resilient=True,
        investigate=profile.get('investigate', False),
        runtime_captures=tuple(config.get('runtime_captures', {}).get(function, [])),
        memory_context=config.get('binary_data_memory', {}).get(function),
        strategy_brief=profile.get("brief", "") + "\nPrevious measured outcome: "
            + json.dumps(node.get("last_outcome", {}), sort_keys=True)
            + '\nShared hypotheses: ' + json.dumps(node.get('shared_context', {}), sort_keys=True)
            + campaign_data.prompt(config, function))["result"]
    return {**result, "attempt_id": result["best_attempt_id"], "source": result["best_source_path"],
            "source_sha256": result["best_source_sha256"], "residual": result["best_residual"],
            "score": result["best_residual"]["weighted_progress_score"]}


def _pins(project: Path, repo: Path) -> dict:
    paths = frozen_wavefront.code_paths(project)
    paths += [repo / p for p in ("Makefile", "symbol_addrs.txt", "snowboardkids.yaml", "snowboardkids.z64",
                                 "tools/m2ctx.py", "tools/textconv.py", "tools/charmap.txt")]
    paths += list((repo / "include").rglob("*.h")) + list((repo / "asm").rglob("*.s"))
    paths += list((repo / "tools/ido-recomp/linux").glob("*"))
    paths += list((repo / "tools/claude-decomp-env").glob("*"))
    checker = shutil.which("clang")
    if checker:
        paths.append(Path(checker).resolve())
    # Hash integration destinations as opaque build inputs; never feed their
    # reference C bodies to the model. Edits elsewhere in the game invalidate
    # a reused integration verdict too, not just edits to the replaced TU.
    paths += [p for p in (repo / "src").rglob("*") if p.suffix in {".c", ".h", ".s"}]
    return frozen_wavefront.file_hashes(paths)


def run(*, repo: Path, db: Path, project: Path, state_path: Path, functions: tuple[str, ...] = (),
        resume: bool = False, max_work_items: int = 12, model_calls: int = 2,
        model: str = "gpt-oss:20b", endpoint: str | None = None, timeout: int = 1200,
        num_predict: int = 6000, seeds: dict | None = None, integrate: bool = False,
        fork_from: Path | None = None, compile_sweep: bool = False,
        scheduler: str = 'legacy', capability_tasks: Path | None = None,
        runtime_captures: Path | None = None, cleanup_exact: bool = False) -> dict:
    if max_work_items < 0 or model_calls < 0:
        raise ValueError("negative budget")
    if scheduler not in {'legacy', 'evidence-v1', 'investigation-v1'}:
        raise ValueError('unknown scheduler')
    endpoint = endpoint or llm.host()
    config = {"repo": str(repo), "db": str(db), "project": str(project), "model": model,
              "endpoint": endpoint, "model_calls": model_calls, "timeout": timeout, "num_predict": num_predict,
              "compile_sweep": compile_sweep,
              "resilient_repair":True,"semantic_cases":64,"semantic_exploration_cases":5000,
              "semantic_steps":10000}
    # Preserve byte-for-byte legacy configuration identity on resume.
    if scheduler != 'legacy':
        config['scheduler'] = scheduler
    if cleanup_exact:
        config['cleanup_exact'] = True
    if runtime_captures:
        manifest = json.loads(runtime_captures.read_text())
        config['runtime_captures'] = {}
        from solver import runtime_capture
        import yaml
        game = yaml.safe_load((repo / 'snowboardkids.yaml').read_text())
        for function, paths in manifest.items():
            records = [json.loads((runtime_captures.parent / path).read_text()) for path in paths]
            for record in records:
                runtime_capture.verify(record, repo / game['options']['target_path'])
                if record['plan']['function'] != function:
                    raise ValueError('runtime capture manifest function mismatch')
            config['runtime_captures'][function] = records
    if capability_tasks:
        if scheduler != 'investigation-v1':
            raise ValueError('capability tasks require investigation-v1')
        config['capability_tasks'] = json.loads(capability_tasks.read_text())
        from eval.capability_repair import validate_task
        for key, task in config['capability_tasks'].items():
            if task.get('issue_key') != key:
                raise ValueError('capability task key differs from issue identity')
            validate_task(task, project)
    prior = None
    if fork_from:
        if resume or seeds:
            raise ValueError("fork-from cannot be combined with resume or seeds")
        prior = json.loads(fork_from.read_text())
        if prior["config"]["repo"] != str(repo) or prior["config"]["db"] != str(db):
            raise ValueError("cannot fork across game/DB identities")
        if functions and set(functions) != set(prior["nodes"]):
            raise ValueError("fork must preserve cohort; start a new campaign for a different cohort")
        functions = tuple(prior["nodes"])
        seeds = {name: n["attempt_id"] for name, n in prior["nodes"].items() if n.get("attempt_id")}
    with campaign_lock(state_path.with_suffix(".lock")):
        pins = _pins(project, repo)
        if resume:
            state = json.loads(state_path.read_text())
            if config != state["config"] or (functions and set(functions) != set(state["nodes"])):
                raise ValueError("resume configuration/cohort differs; fork a new campaign explicitly")
            # Per-function targets are discovered during intake, not part of
            # the initial global inventory. Rehash them on every resume.
            targets = [Path(path) for path in state["pins"]
                       if Path(path).is_relative_to(repo / "nonmatchings")]
            pins.update(frozen_wavefront.file_hashes(targets))
            with sqlite3.connect(db) as conn:
                inventory = list(conn.execute("SELECT name,addr,size,insn_count FROM functions ORDER BY name"))
            if pins != state["pins"] or digest(inventory) != state["inventory_sha256"]:
                state["status"] = "paused_inputs_changed"
                agentrepair._atomic_json(state_path, state)
                return state
        else:
            if state_path.exists():
                raise ValueError("campaign exists; use --resume")
            with sqlite3.connect(db) as conn:
                inventory = list(conn.execute("SELECT name,addr,size,insn_count FROM functions ORDER BY name"))
                deps, _ = callgraph.edges(conn)
                units = translation_units(conn)
            selected = set(functions) if functions else {row[0] for row in inventory}
            unknown = selected - {row[0] for row in inventory}
            if unknown:
                raise ValueError("unknown functions: " + ", ".join(sorted(unknown)))
            excluded = []
            for name in sorted(selected):
                try:
                    agentrepair._refuse_frozen_heldout(project / "eval/sets", name)
                except ValueError:
                    if functions:
                        raise
                    excluded.append(name)
            selected.difference_update(excluded)
            if not selected:
                raise ValueError("empty eligible cohort")
            levels, _ = evidence_schedule.levels({name: set(deps.get(name, ())) & selected for name in selected})
            state = {"kind": "resumable-completion-campaign", "schema_version": 1,
                "regime": "header-assisted development; explicit historical seeds are not clean replay",
                "config": config, "pins": pins, "status": "running", "complete_c_decompilation": False,
                "inventory_count": len(inventory), "excluded_heldout": excluded, "integrations": [],
                "created_at": time.time(), "inventory_sha256": digest(inventory),
                "forked_from": {"path": str(fork_from), "sha256": digest(prior)} if prior else None,
                "nodes": {name: {"status": "pending", "jobs": [], "dag_level": levels[name],
                    "address": address, "size": size, "instruction_count": count,
                    "seed_attempt_id": (seeds or {}).get(name),
                    "seed_frontier": retained_candidates(prior["nodes"][name]) if prior else []}
                    for name, address, size, count in inventory if name in selected}}
            if scheduler in {'evidence-v1', 'investigation-v1'}:
                state['dependency_graph'] = repair_queue.graph(deps, selected)
                # Ordering locality only. An older KB without unit metadata
                # simply schedules as before; nothing here gates or reserves work.
                index = {name: unit for name, unit in units.items() if name in selected}
                if index:
                    state['tu_index'] = index
                    state['tu_index_provenance'] = {
                        'algorithm': 'function-range-clusters-v2',
                        'inventory_sha256': state['inventory_sha256'],
                        'index_sha256': digest(index),
                        'role': 'ordering heuristic; recorded ELF ranges may be assisted metadata; '
                                'no reference TU assignment or interface authority'}
        model_pin = frozen_wavefront.model_digest(endpoint, model) if model_calls else None
        if "model_digest" in state and state["model_digest"] != model_pin:
            raise ValueError("model changed; fork a new campaign")
        state["model_digest"] = model_pin
        artifacts = state_path.parent / (state_path.stem + "-artifacts")
        artifacts.mkdir(parents=True, exist_ok=True)
        agentrepair._atomic_json(state_path, state)
        for _ in range(max_work_items):
            # A service pause takes effect between durable work items. The
            # supervisor preserves the marker across logons and restarts.
            if (state_path.parent / 'service.pause').exists():
                break
            if scheduler in {'evidence-v1', 'investigation-v1'}:
                state['repair_queue'], _ = repair_queue.project(state, PROFILES)
            work = choose(state)
            if work is None:
                break
            function, profile = work
            node = state["nodes"][function]
            # An interrupted work item is resumed from its durable result if
            # present, otherwise rerun under a fresh artifact name. Model calls
            # can be duplicated after a hard crash; never claim exactly-once.
            pending = state.get("inflight")
            if pending and (pending['function'] != function or pending['profile'] != profile['name']):
                raise ValueError('inflight work differs from scheduler selection; refusing receipt reassignment')
            if pending and pending.get('evidence_key') not in {None, profile.get('evidence_key')}:
                raise ValueError('inflight evidence differs; refusing stale receipt reassignment')
            out = Path(pending["receipt"]) if pending else artifacts / f"{time.time_ns()}-{function}.json"
            state["inflight"] = {"function": function, "profile": profile["name"], "receipt": str(out)}
            if 'evidence_key' in profile:
                state['inflight']['evidence_key'] = profile['evidence_key']
            agentrepair._atomic_json(state_path, state)
            started = time.monotonic()
            try:
                frozen_wavefront.verify_files(pins)
                if out.exists():
                    result = json.loads(out.read_text())
                else:
                    result = execute(repo=repo, db=db, function=function, node=node,
                                     profile=profile, config=config, out=out)
                    result["wall_seconds"] = round(time.monotonic() - started, 3)
                    agentrepair._atomic_json(out, result)
                frozen_wavefront.verify_files(pins)
            except frozen_wavefront.FrozenInputChanged as exc:
                state.update(status="paused_inputs_changed", error=str(exc))
                agentrepair._atomic_json(state_path, state)
                return state
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                result = {"status": "parked", "blocker": {"status": "operational_or_intake_failure",
                          "error": f"{type(exc).__name__}: {exc}"}}
                from solver.compiler_recipe import ObjectBackendRequired
                if isinstance(exc,ObjectBackendRequired):
                    result['blocker'].update(status='object_postprocessing_backend_required',
                                             evidence=exc.evidence)
                agentrepair._atomic_json(out, result)
            accept(node, profile, result, out)
            if scheduler == 'investigation-v1' and result.get('investigation'):
                from solver import shared_hypotheses
                with sqlite3.connect(db) as shared_conn:
                    shared_hypotheses.ingest(state, function, result['investigation'], shared_conn)
            state.pop("inflight", None)
            # New target files join the frozen input set only after intake.
            if profile["name"] == "intake" and node.get("source"):
                ws = repo / "nonmatchings" / function
                paths = list(ws.glob("target*")) + list(ws.glob(".compiler-*.json")) + list(ws.glob(".compiler-*.sh"))
                state["pins"].update(frozen_wavefront.file_hashes(paths))
                pins = state["pins"]
            state["status"] = status(state)
            if scheduler in {'evidence-v1', 'investigation-v1'}:
                state['repair_queue'], _ = repair_queue.project(state, PROFILES)
            agentrepair._atomic_json(state_path, state)
            print(json.dumps({"function": function, "profile": profile["name"],
                              "status": node["status"], "score": node.get("score")}), flush=True)
        if cleanup_exact:
            from eval import finish_candidate
            for name, node in state['nodes'].items():
                if node['status'] != 'object_exact' or node.get('cleanup', {}).get('source_sha256') == node.get('source_sha256'):
                    continue
                frozen_wavefront.verify_files(pins)
                finish_candidate.clean(repo, db, name, node, artifacts / f'{time.time_ns()}-{name}-cleanup.json')
                frozen_wavefront.verify_files(pins)
                agentrepair._atomic_json(state_path, state)
        if integrate:
            entries = [{"function": name, "attempt_id": node["attempt_id"], "source": node["source"],
                        "verification": node["verification"]} for name, node in state["nodes"].items()
                       if node["status"] in {"object_exact", "function_exact_pending_integration"}]
            if entries:
                tag = str(time.time_ns())
                entries, blocked = preflight_integration(repo=repo, db=db, entries=entries, artifacts=artifacts, tag=tag)
                state["integrations"].extend(blocked)
            if entries:
                integrated, records = integrate_candidates(repo=repo, db=db, entries=entries, artifacts=artifacts)
                state["integrations"].extend(records)
                for item in integrated:
                    state["nodes"][item["function"]]["status"] = "integrated"
        state["status"] = status(state)
        state["summary"] = {
            "cohort_functions": len(state["nodes"]),
            "object_exact_or_integrated": sum(n["status"] in {"object_exact", "integrated"} for n in state["nodes"].values()),
            "integrated": sum(n["status"] == "integrated" for n in state["nodes"].values()),
            "function_exact_pending_integration": sum(n["status"] == "function_exact_pending_integration"
                                                       for n in state["nodes"].values()),
            "parked": sum(n["status"] == "parked" for n in state["nodes"].values()),
            "stalled": sum(n["status"] == "pending" and
                scheduled_profile(state, n) is None for n in state["nodes"].values()),
            "work_remaining": choose(state) is not None,
            "complete_c_decompilation": False,
        }
        state['compile_coverage'] = {name: {
            'compiled': n.get('residual', {}).get('compiled'),
            'frontend': (n.get('residual', {}).get('frontend') or {}).get('status'),
            'model_visits': sum(j.get('model', False) or j['profile'] in {p['name'] for p in PROFILES if p['model']}
                                or (j['profile'] == 'semantic_handoff' and model_calls > 0) for j in n['jobs']),
            'next_profile': (scheduled_profile(state, n) or {}).get('name'),
            'state': n['status']} for name, n in state['nodes'].items()}
        if scheduler in {'evidence-v1', 'investigation-v1'}:
            state['repair_queue'], _ = repair_queue.project(state, PROFILES)
        agentrepair._atomic_json(state_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "db", "state"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--functions", nargs="*", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--integrate", action="store_true")
    parser.add_argument("--compile-sweep", action="store_true",
                        help="visit every compile/frontend failure before spending work on byte polish")
    parser.add_argument('--scheduler', choices=('legacy', 'evidence-v1', 'investigation-v1'), default='legacy',
                        help='evidence-directed repair queue; opt in on a new/forked campaign')
    parser.add_argument('--capability-tasks', type=Path,
                        help='trusted issue-keyed reproductions for isolated engineering experiments')
    parser.add_argument('--runtime-captures', type=Path, help='function -> capture paths JSON manifest')
    parser.add_argument('--cleanup-exact', action='store_true', help='verify readability cleanup before optional integration')
    parser.add_argument("--seeds", type=Path, help="explicit JSON function -> historical attempt ID map")
    parser.add_argument("--fork-from", type=Path, help="retain prior bests, reset verification/strategy eligibility explicitly")
    parser.add_argument("--max-work-items", type=int, default=12)
    parser.add_argument("--model-calls", type=int, default=2)
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument("--endpoint")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--num-predict", type=int, default=6000)
    args = vars(parser.parse_args())
    args["state_path"] = args.pop("state").resolve()
    args["functions"] = tuple(args["functions"])
    args["seeds"] = json.loads(args["seeds"].read_text()) if args["seeds"] else None
    for name in ("repo", "db", "project"):
        args[name] = args[name].resolve()
    result = run(**args)
    print(json.dumps({"status": result["status"], "nodes": len(result["nodes"]),
                      "complete_c_decompilation": result["complete_c_decompilation"]}))


if __name__ == "__main__":
    main()
