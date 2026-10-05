"""Export an immutable development set from a measured intake frame.

WHAT THIS IS FOR. The audit's corrected table left 16 (now 19) states that compile AND pass the clang
frontend without being byte-exact -- candidates that are one or two real repairs from done, as opposed to
candidates that only satisfy IDO's parser. That set is what Phase B measures against: can the existing
actions, composed, finish one of them?

WHY IT IS AN EXPORT AND NOT A RE-RUN. The frame is a measurement of SPECIFIC BYTES. Re-deriving a
candidate by replaying the sequence would produce something that looks the same and is not the thing that
was scored, and the substitution would be invisible -- exactly the failure a dev set exists to prevent. So
each row carries the sha256 of the candidate the probe compiled (`sequence.final_sha256`), this module
looks that hash up in the attempt log, and a row whose bytes cannot be produced from provenance is
reported as UNRECOVERABLE and fails the export rather than being quietly replaced.

WHAT EACH ENTRY CARRIES:
    source          the recovered bytes, verbatim
    sha256          the hash those bytes must have (checked on read, not assumed)
    fresh           a NEW compile verdict for the recovered source, so "it compiled then" is never the
                    only evidence
    target          the translation-unit target the compile used
    compiler        the compiler recipe identity the oracle resolved
    assistance      `binary-only` | `header-assisted` | `reference-source-assisted`, from what the winning
                    sequence actually leaned on -- never inferred from the function's name
    lineage         which frames/actions produced it, and the intermediate hashes
    exclusion       why this task may not be used for training (all of them are: this is dev data)

THE FRAME IS DEVELOPMENT DATA AND IS NOT A SEALED TEST SET. The 200 states were drawn from the KB and are
visible; the `test` split stays where it is. This module refuses to export a task that is in the sealed
split, because a dev set that quietly contains held-out functions is the contamination this project's
whole evaluation story rests on avoiding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1

# WHAT A STEP LEANS ON, as a tier rather than a boolean. The order is by strength of the claim: a
# candidate whose winning sequence needed a declaration recovered from the target's own `src/` cannot be
# reported as binary-only, whatever its score.
ASSISTANCE_TIERS = ("binary-only", "header-assisted", "reference-source-assisted")
REFERENCE_SOURCE_STEPS = ("source_type_declarations",)
HEADER_STEPS = ("header_variant", "globals_variant")

# THE REGIME IS PART OF THE ARTIFACT. A reader must not have to infer from a path whether these numbers
# may be quoted as capability, and the answer here is never.
REGIME = ("DEVELOPMENT DATA from the exposed 200-state frame. Never a sealed test set, never training "
          "data. Assistance tiers are recorded per entry and a reference-source-assisted entry is not "
          "binary-only evidence.")


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def assistance_tier(steps: list[str]) -> dict:
    """The tier the winning sequence earned, with the steps that put it there.

    A struct DECLARATION recovered from the reference decomp's `src/*.c` is shared vocabulary and is
    allowed, but a match leaning on it is not `SOLVED` and not binary-only; `include/**` is admitted on the
    same basis. Both are named here instead of being folded into a pass.
    """
    reference = [step for step in steps if step in REFERENCE_SOURCE_STEPS]
    header = [step for step in steps if step in HEADER_STEPS]
    if reference:
        tier = "reference-source-assisted"
    elif header:
        tier = "header-assisted"
    else:
        tier = "binary-only"
    return {"tier": tier, "reference_source_steps": reference, "header_steps": header,
            "note": ("a declaration copied from the reference decomp's src/** or include/** is shared "
                     "vocabulary, not a body; a result that needed one is never reported as binary-only "
                     "and never as SOLVED")}


def recover_source(conn: sqlite3.Connection, source_sha256: str) -> tuple[str | None, dict]:
    """The exact bytes with this hash, from the attempt log, or None and why not."""
    if not source_sha256:
        return None, {"reason": "the frame row records no final candidate hash"}
    rows = conn.execute(
        "select source_code, source_sha256, iteration, run_id from attempts "
        "where source_sha256 = ? and source_code is not null "
        "order by iteration desc limit 5", (source_sha256,)).fetchall()
    if not rows:
        return None, {"reason": f"no attempt row carries source_sha256 {source_sha256[:16]}..."}
    for source, recorded, iteration, run_id in rows:
        if sha256_text(source) == source_sha256:
            return source, {"attempt_iteration": iteration, "attempt_run_id": run_id,
                            "rows_with_this_hash": len(rows)}
    return None, {"reason": f"{len(rows)} row(s) carry the hash but none of their bytes hash to it"}


def winning_steps(row: dict) -> list[str]:
    """Which sequence steps actually changed the candidate, in the order the probe applied them."""
    fired = [key.split(".")[-1] for key, value in (row.get("actions") or {}).items()
             if value.get("changed")]
    return fired


def plan(frame: dict, *, sealed: set[str]) -> dict:
    """Select the development rows from a measured frame, before anything is recovered."""
    selected, excluded = [], []
    for row in frame["rows"]:
        name = row["function"]
        sequence = row.get("sequence") or {}
        reason = None
        if name in sealed:
            reason = "in the sealed test split"
        elif sequence.get("exact"):
            reason = "already byte-exact (nothing to finish)"
        elif not sequence.get("compiled"):
            reason = "does not compile (different problem: the intake route, not the finish)"
        elif not sequence.get("frontend_passed"):
            reason = "compiles but the clang frontend rejects it"
        if reason:
            excluded.append({"function": name, "reason": reason})
            continue
        selected.append(row)
    return {"selected": selected, "excluded": excluded}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frame", type=Path,
                    default=ROOT / "eval/results/intake-20260921/wide-intake-acceptance2.json")
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--splits", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/splits.json")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/dev-set-20260921/dev-set.json")
    ap.add_argument("--sources", type=Path,
                    default=ROOT / "eval/results/dev-set-20260921/sources")
    ap.add_argument("--no-fresh-compile", action="store_true",
                    help="skip the recompile (the export is then NOT a finished artifact)")
    args = ap.parse_args(argv)

    frame = json.loads(args.frame.read_text(encoding="utf-8"))
    sealed = set((json.loads(args.splits.read_text(encoding="utf-8")) or {}).get("test") or [])
    selection = plan(frame, sealed=sealed)
    conn = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
    entries, unrecoverable = [], []
    try:
        for row in selection["selected"]:
            name, sequence = row["function"], row["sequence"]
            source, provenance = recover_source(conn, sequence.get("final_sha256"))
            if source is None:
                unrecoverable.append({"function": name,
                                      "expected_sha256": sequence.get("final_sha256"),
                                      "recovery": provenance})
                continue
            steps = winning_steps(row)
            entry = {
                "function": name, "sha256": sequence["final_sha256"],
                "recovered_from": provenance,
                "source_chars": len(source),
                "recorded": {"ido_compiled": bool(sequence.get("compiled")),
                             "frontend_passed": bool(sequence.get("frontend_passed")),
                             "exact": bool(sequence.get("exact")), "score": sequence.get("score"),
                             "frontend_classes": sequence.get("frontend_classes") or []},
                "assistance": assistance_tier(steps),
                "lineage": {"frame": str(args.frame), "frame_schema": frame.get("schema_version"),
                            "baseline_draft_sha256": row.get("draft_sha256"),
                            "size": row.get("size"), "tier": row.get("tier"),
                            "failure_class": row.get("failure_class"),
                            "sequence_steps": list((row.get("actions") or {}).keys()),
                            "steps_that_changed": steps,
                            "measurement_seconds": frame.get("seconds")},
                # EVERY ENTRY IS EXCLUDED FROM TRAINING, and the reason is the same for all of them: this
                # is the development panel a search is measured on. A set that trains the policy it grades
                # measures nothing.
                "training_eligible": False,
                "training_exclusion": "development panel: measured on, never trained on",
            }
            entries.append(entry)
    finally:
        conn.close()

    args.sources.mkdir(parents=True, exist_ok=True)
    # THE SOURCE IS WRITTEN OUT AND THEN RE-READ. The bytes on disk are the artifact a later stage
    # compiles, so the digest recorded against them is taken from the file, not from the variable that
    # produced it -- a set whose manifest hash describes something other than its own contents is worse
    # than no manifest.
    from eval.tool_agent_run import build_context
    repo = Path.home() / "decomp/sbk1"
    for entry in entries:
        path = args.sources / f"{entry['function']}.c"
        try:
            path.write_text(recover_source_readonly(args.kb, entry["sha256"]), encoding="utf-8")
        except Exception as exc:                                # noqa: BLE001
            entry["fresh"] = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            continue
        on_disk = sha256_text(path.read_text(encoding="utf-8"))
        entry["source_sha256_on_disk"] = on_disk
        if on_disk != entry["sha256"]:
            entry["fresh"] = {"status": "error",
                              "reason": "the bytes written to disk do not hash to the recorded digest"}
            continue
        if args.no_fresh_compile:
            entry["fresh"] = {"status": "not-compiled",
                              "reason": "--no-fresh-compile: this export is NOT a finished artifact"}
            continue
        conn = sqlite3.connect(str(args.kb))
        try:
            context, why = build_context(repo, entry["function"], conn=conn)
            if context is None:
                entry["fresh"] = {"status": "unavailable", "reason": why}
                continue
            verdict = context.compile_fn(entry_source(path))
            entry["fresh"] = {
                "status": "compiled", "exact": bool(verdict.get("exact")),
                "score": verdict.get("score"), "compiled": bool(verdict.get("compiled")),
                "stderr_head": (verdict.get("stderr") or "")[:200],
                "target": (verdict.get("compiler_recipe") or {}).get("target"),
                "compiler_recipe": verdict.get("compiler_recipe"),
            }
        except Exception as exc:                                # noqa: BLE001
            entry["fresh"] = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        finally:
            conn.close()

    payload = {"schema_version": SCHEMA_VERSION, "kind": "intake-development-set",
               "frame": str(args.frame), "frame_functions": len(frame["rows"]),
               "registry": "eval.tool_registry.ACTIONS",
               "selection": {"selected": len(entries), "excluded": len(selection["excluded"]),
                             "unrecoverable": len(unrecoverable),
                             "excluded_detail": selection["excluded"],
                             "unrecoverable_detail": unrecoverable},
               "regime": REGIME,
               "entries": entries}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"frame": str(args.frame), "selected": len(entries),
                      "excluded": len(selection["excluded"]), "unrecoverable": len(unrecoverable),
                      "unrecoverable_detail": unrecoverable,
                      "tiers": {tier: sum(1 for e in entries
                                          if e["assistance"]["tier"] == tier)
                                for tier in ASSISTANCE_TIERS},
                      "out": str(args.out)}, indent=2))
    # AN UNRECOVERABLE ROW IS A FAILURE, NOT A SMALLER SET. The exit status is what stops a caller from
    # reporting a rate over a set that silently lost its hardest members.
    return 1 if unrecoverable else 0


def recover_source_readonly(kb: Path, source_sha256: str) -> str:
    conn = sqlite3.connect(f"file:{kb}?mode=ro", uri=True)
    try:
        source, _ = recover_source(conn, source_sha256)
    finally:
        conn.close()
    if source is None:
        raise LookupError(f"no attempt row carries the bytes for {source_sha256[:16]}...")
    return source


def entry_source(path: Path) -> str:
    """The bytes that were written out, re-read. Compiling anything else measures another candidate."""
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
