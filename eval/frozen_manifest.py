"""The frozen evaluation boundary: what a freeze has to bind before a panel can be evaluated.

WHY THIS IS ITS OWN MODULE
--------------------------
`eval.evaluate_source_repair` owns the run; this module owns the QUESTION "is this the frozen
experiment?", and answers it before any model is loaded. Splitting it out keeps the two testable
apart, and it is the piece an independent audit broke.

THE DEFECT IT CLOSES
--------------------
The loader used to check ids, counts and `frozen_at`. An audit then swapped one task's
`input.candidate` for that task's own hidden answer -- turning a held-out repair task into a copy
of its solution -- and the evaluation ran on it, because nothing tied the manifest to the CONTENT
of the records it named. A freeze that names ids and nothing else is a table of contents, not a
freeze.

WHAT A FREEZE NOW BINDS, AND WHAT THIS MODULE CHECKS
----------------------------------------------------
  the manifest itself   `manifest_sha256` must recompute, so an edited manifest is refused;
  the dataset file      its bytes must hash to the frozen `dataset_sha256`, and it must hold the
                        frozen number of records;
  every record          a per-record content hash (whole record, solver-visible input, answer),
                        so a candidate or an answer cannot be swapped after the freeze;
  the panel's shape     no duplicate ids, no id in two splits, counts that agree with the id
                        lists, and no record outside the panel it was frozen into;
  the compiler          the recipe in the manifest must be the recipe actually being used
                        (`command_sha256`, compiler, target);
  the model input       the FINAL RENDERED PROMPT is leakage-checked, for every task, before the
                        GPU is touched.

A manifest written before content hashing (schema 2) cannot be checked at all, so it is refused
with an explicit "re-freeze it" error rather than silently accepted or silently failing.

WHAT "BOUND" DOES AND DOES NOT MEAN
-----------------------------------
`record_hashes` bind a SPECIFIC BUILD, not just the experiment. IDO objects embed the absolute
source path and a random `asm_processor` temp filename, so recompiling the same C produces
different object bytes: `parent.object_sha256`, `target.object_sha256` and `provenance.built_at`
differ in EVERY record of a rebuilt panel, and so does the manifest digest. Nothing experimental
moved -- none of those three fields is read by `eval.evaluate_source_repair`,
`eval.frozen_manifest`, `eval.train_source_repair` or `eval.posttraining_gate`, and the evaluator
recompiles the target from the answer rather than trusting a stored digest. So a manifest digest
that changes after a rebuild is NOT drift in the experiment; to decide whether a rebuilt panel is
the same experiment, compare the solver-visible `input.*`, the answers and the compiler recipe
(which is what `eval/results/local-posttraining-20260920/evaluation-certified/PANEL-IDENTITY.md`
does for the rebuilt post-training panel). Only when those differ has the panel changed.

WHAT THIS DOES NOT CLAIM
------------------------
It does not claim the frozen tasks are correct or that the answers are right; it claims the run
is the one that was declared. It also does not decide exactness -- that is
`solver.byte_certificate.certify`, reached through `eval.repair_dataset_synth.certify_exact`.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_REPO = Path.home() / "decomp" / "sbk1"


# --- the manifest's own integrity ----------------------------------------------

def manifest_content_sha256(manifest: dict) -> str:
    """The digest `freeze_manifest` recorded: everything except the digest field itself."""
    body = {k: v for k, v in manifest.items() if k != "manifest_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def verify_manifest_integrity(manifest: dict, manifest_path: Path) -> None:
    """The manifest's own digest must recompute, or it was edited after it was frozen."""
    recorded = manifest.get("manifest_sha256")
    if not recorded:
        raise SystemExit(f"{manifest_path} carries no manifest_sha256; it attests to nothing")
    recomputed = manifest_content_sha256(manifest)
    if recomputed != recorded:
        raise SystemExit(
            f"{manifest_path} was modified after it was frozen: manifest_sha256 records "
            f"{recorded[:16]}... but the file now hashes to {recomputed[:16]}... A frozen "
            f"specification that can be edited is not frozen; re-freeze it deliberately.")


def verify_content_bound(manifest: dict, manifest_path: Path) -> None:
    """Refuse a manifest that predates per-record content hashing, with the remedy stated."""
    if manifest.get("record_hashes") and manifest.get("dataset_sha256"):
        return
    from eval.repair_dataset_synth import MANIFEST_SCHEMA_VERSION
    version = manifest.get("schema_version")
    raise SystemExit(
        f"{manifest_path} is manifest schema {version} and carries no per-record content "
        f"hashes (schema {MANIFEST_SCHEMA_VERSION} added `record_hashes`, `dataset_sha256` and "
        f"`dataset_lines`). THIS MANIFEST PREDATES CONTENT HASHING: it cannot bind the records it "
        f"names, so a task's candidate or answer could have been swapped after the freeze and "
        f"nothing here could tell. Re-freeze it -- delete the manifest and rebuild the dataset "
        f"with `python -m eval.repair_dataset_synth` -- before evaluating against it.")


# --- the panel -----------------------------------------------------------------

def verify_splits(manifest: dict, manifest_path: Path) -> None:
    """Duplicate ids, two-split ids and counts that disagree with the id lists are all refused."""
    ids = manifest.get("task_ids") or {}
    counts = manifest.get("counts") or {}
    if not ids:
        raise SystemExit(f"{manifest_path} lists no task ids in any split")
    for name, listed in ids.items():
        duplicated = sorted({tid for tid in listed if listed.count(tid) > 1})
        if duplicated:
            raise SystemExit(
                f"{manifest_path} lists {len(duplicated)} duplicate id(s) in split {name!r}: "
                f"{duplicated[:5]}; a panel that names a task twice is ambiguous")
    overlap = sorted(set(ids.get("train") or []) & set(ids.get("test") or []))
    if overlap:
        raise SystemExit(
            f"{len(overlap)} task id(s) appear in two splits: {overlap[:5]}. A task that is both "
            f"held out and trained on makes every number computed from it fiction")
    for name, listed in ids.items():
        if name in counts and counts[name] != len(listed):
            raise SystemExit(f"{manifest_path} counts say {counts[name]} {name} task(s) but "
                             f"lists {len(listed)}")
    total = counts.get("total")
    if total is not None and total != sum(len(v) for v in ids.values()):
        raise SystemExit(f"{manifest_path} counts say {total} task(s) in total but the id lists "
                         f"hold {sum(len(v) for v in ids.values())}")


def verify_dataset_digest(manifest: dict, dataset_path: Path, raw: bytes) -> None:
    """The dataset file's bytes must be the ones that were frozen."""
    recorded = manifest.get("dataset_sha256")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != recorded:
        raise SystemExit(
            f"{dataset_path} is not the dataset this manifest froze: it hashes to "
            f"{actual[:16]}... but the manifest records {str(recorded)[:16]}... "
            f"({len(raw)} bytes). The dataset was rebuilt or edited after the freeze.")
    lines = manifest.get("dataset_lines")
    if lines is not None and len([l for l in raw.decode("utf-8").splitlines() if l.strip()]) != lines:
        raise SystemExit(f"{dataset_path} does not have the {lines} record(s) the manifest froze")


def verify_dataset_membership(manifest: dict, tasks: list[dict], manifest_path: Path) -> None:
    """Every dataset record belongs to exactly one declared split, and the record agrees."""
    listed: dict[str, str] = {}
    for split, split_ids in (manifest.get("task_ids") or {}).items():
        for task_id in split_ids:
            listed[task_id] = split
    seen = [task.get("task_id") for task in tasks]
    duplicated = sorted({tid for tid in seen if seen.count(tid) > 1})
    if duplicated:
        raise SystemExit(
            f"the dataset contains {len(duplicated)} duplicated task id(s): {duplicated[:5]}. "
            f"One id, one record: a duplicate makes 'which candidate ran' ambiguous")
    by_id = {task.get("task_id"): task for task in tasks}
    # A frozen task the dataset does not contain is reported FIRST: it is a fact about the frozen
    # panel, while an unlisted row is a fact about the file, and the panel is what is being
    # evaluated. Both are refused.
    missing = sorted(set(listed) - set(by_id))
    if missing:
        raise SystemExit(f"the manifest froze {len(missing)} task(s) the dataset does not "
                         f"contain: {missing[:5]}")
    unlisted = sorted(set(by_id) - set(listed))
    if unlisted:
        raise SystemExit(
            f"the dataset contains {len(unlisted)} task(s) the manifest never froze: "
            f"{unlisted[:5]}. A record outside the frozen panel was not part of the experiment")
    for task_id, split in listed.items():
        row = by_id.get(task_id)
        if row is not None and row.get("split") != split:
            raise SystemExit(
                f"{task_id} is listed in split {split!r} but the record says {row.get('split')!r}")


def verify_record_hashes(manifest: dict, selected: list[dict]) -> None:
    """Bind each selected record to the content hash the freeze recorded for it.

    This is the check the audit's swap walked through: `input.candidate` replaced by the task's
    hidden answer, with the manifest untouched. The candidate lives inside `input`, so the
    `content` digest and the `input` digest both change, and the answer digest catches the
    mirror-image edit.
    """
    from eval.repair_dataset_synth import record_hashes
    recorded = manifest.get("record_hashes") or {}
    detail = {"input": "the task's solver-visible input (assembly/candidate/feedback)",
              "answer": "the task's answer",
              "content": "the whole record"}
    for task in selected:
        task_id = task.get("task_id")
        frozen = recorded.get(task_id)
        if not frozen:
            raise SystemExit(f"{task_id} is listed in the manifest but has no frozen content "
                             f"hash; the manifest cannot vouch for it")
        actual = record_hashes(task)
        # EVERY field that moved is reported, not just the first one checked: "the answer changed"
        # and "the whole record changed" are the same edit seen at two resolutions, and a reader
        # deciding whether to re-freeze needs the specific one.
        changed = [field for field in ("input", "answer", "content")
                   if frozen.get(field) != actual.get(field)]
        if changed:
            described = "; ".join(
                f"{detail[field]} changed after it was frozen (frozen "
                f"{str(frozen.get(field))[:16]}..., now {str(actual.get(field))[:16]}...)"
                for field in changed)
            raise SystemExit(
                f"{task_id}: {described}. A record that is not the one that was frozen cannot be "
                f"evaluated as if it were; re-freeze the dataset deliberately if the change is "
                f"intended.")


def verify_compiler_identity(manifest: dict, recipe: dict | None, manifest_path: Path) -> list[str]:
    """The recipe in the manifest must be the recipe being used, or the freeze is of another build.

    `command_sha256` is the load-bearing field: it digests the resolved compiler invocation and
    its flags, which is what decides codegen. `makefile_sha256`/`projection_sha256` changes are
    reported rather than fatal, because a Makefile edit whose resolved command is unchanged does
    not change a single emitted instruction.
    """
    recorded = manifest.get("compiler_recipe") or {}
    if not recorded:
        raise SystemExit(f"{manifest_path} records no compiler recipe")
    if recipe is None:
        raise SystemExit(
            "no compiler recipe was resolved to check the frozen one against, so the manifest's "
            "compiler identity is unverified; pass the recipe the evaluation will actually use")
    mismatches = []
    for field in ("command_sha256", "compiler", "target"):
        if recorded.get(field) != recipe.get(field):
            mismatches.append(f"{field}: frozen {recorded.get(field)!r} != used {recipe.get(field)!r}")
    if recorded.get("command") is not None and recipe.get("command") is not None \
            and recorded["command"] != recipe["command"]:
        mismatches.append("command: the frozen command line differs from the one being used")
    if mismatches:
        raise SystemExit(
            f"the compiler identity recorded in {manifest_path} is not the one being used: "
            + "; ".join(mismatches))
    return [f"{field} differs from the resolved recipe, but the resolved command "
            f"(`command_sha256`) is identical, so codegen is unchanged"
            for field in ("makefile_sha256", "projection_sha256")
            if recorded.get(field) is not None and recorded.get(field) != recipe.get(field)]


# --- the model-input boundary ---------------------------------------------------

def task_prompt(task: dict) -> str:
    """THE MODEL INPUT for one task: rendered here, and checked HERE before it is sent.

    The dataset-time leakage check proves the record was clean when it was built. It cannot prove
    the final prompt is clean, because the prompt is rendered later from fields that could have
    been rewritten in between -- and because rendering is the last moment at which the answer can
    still be kept out of the model's context. So the check runs on the rendered string, not on
    the record, and a record that carries no answer is refused rather than checked vacuously:
    "there was nothing to check" is not "there is no leak".
    """
    from eval.repair_dataset_synth import leakage_check_text
    from eval.repair_prompts import synthetic_repair_prompt
    answer = task.get("generator_source") or (task.get("child") or {}).get("source_c") or ""
    if not answer.strip():
        raise SystemExit(
            f"{task.get('task_id')!r} carries no answer, so its prompt cannot be checked for "
            f"leakage and its target cannot be compiled. Refusing rather than evaluating a task "
            f"whose held-out answer is unknown.")
    candidate = (task.get("input") or {}).get("candidate") or ""
    prompt = synthetic_repair_prompt(
        asm=(task.get("input") or {}).get("assembly") or "",
        candidate=candidate,
        feedback=((task.get("input") or {}).get("feedback") or {}).get("text") or "")
    report = leakage_check_text(answer=answer, candidate=candidate, visible=prompt)
    if not report["clean"]:
        raise SystemExit(
            f"the rendered prompt for {task.get('task_id')!r} leaks its answer: "
            f"{report['findings']}. The check runs on the final model input, so this is the "
            f"prompt that would have been sent.")
    return prompt


# --- the loader ------------------------------------------------------------------

def load_frozen_tasks(manifest_path: Path, dataset_path: Path, *, split: str = "test",
                      limit: int = 0, recipe: dict | None = None,
                      repo: Path | None = None) -> tuple[list[dict], dict]:
    """The tasks the manifest froze for this split, verified against everything the freeze bound.

    `recipe` is the compiler provenance the caller is ACTUALLY using (`resolve_recipe(...)`'s
    `provenance`). When it is omitted the recipe is resolved from `repo` (default: the game repo)
    and checked the same way -- the point is that the manifest's compiler identity is compared
    with a real one, never taken on trust.

    Every check raises `SystemExit` with the reason. A tampered panel that is silently accepted is
    worse than a stopped run, and a stopped run is cheap: this happens before the model loads.
    """
    from eval.repair_dataset_synth import resolve_recipe
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    raw = Path(dataset_path).read_bytes()
    tasks = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]

    if not manifest.get("frozen_at"):
        raise SystemExit("the manifest has no frozen_at; it is not a freeze")
    verify_content_bound(manifest, manifest_path)
    verify_manifest_integrity(manifest, manifest_path)
    verify_splits(manifest, manifest_path)
    verify_dataset_digest(manifest, dataset_path, raw)
    verify_dataset_membership(manifest, tasks, manifest_path)

    if split not in (manifest.get("task_ids") or {}):
        raise SystemExit(f"{manifest_path} froze no {split!r} split; it has "
                         f"{sorted((manifest.get('task_ids') or {}))}")
    wanted = manifest["task_ids"][split]
    by_id = {task["task_id"]: task for task in tasks}
    missing = [tid for tid in wanted if tid not in by_id]
    if missing:
        raise SystemExit(f"the manifest froze {len(missing)} task(s) the dataset does not "
                         f"contain: {missing[:5]}")
    selected = [by_id[tid] for tid in wanted]
    if limit:
        selected = selected[:limit]

    # The manifest's own split counts must match what was selected, or the freeze and the data
    # have drifted apart and the evaluation is not of the frozen experiment.
    expected = manifest["counts"][split]
    if len(wanted) != expected:
        raise SystemExit(f"manifest says {expected} {split} tasks but lists {len(wanted)}")

    verify_record_hashes(manifest, selected)

    if recipe is None:
        repo = Path(repo) if repo is not None else DEFAULT_REPO
        target = (manifest.get("compiler_recipe") or {}).get("target")
        if not target:
            raise SystemExit(f"{manifest_path} records no compiler target to resolve")
        recipe = resolve_recipe(repo, target)["provenance"]
    manifest["load_notes"] = verify_compiler_identity(manifest, recipe, manifest_path)

    # THE MODEL-INPUT BOUNDARY. Rendered and checked here, for every task, before any GPU work:
    # a prompt that leaks its answer must stop the run, not be discovered after the model has
    # already been told the answer.
    for task in selected:
        task["prompt_sha256"] = hashlib.sha256(task_prompt(task).encode("utf-8")).hexdigest()
    if not selected:
        raise SystemExit(f"no {split} tasks selected from {manifest_path}")
    return selected, manifest
