"""Shadow-test a binary-derived opaque-struct annotation on one parent.

This is a deliberately narrow follow-up to ``two_lane_wavefront_pilot``.  It
does not read target source or generate C.  The candidate is the unchanged m2c
body plus a prelude whose offsets and widths come from target instructions.
Semantic field names come from the draft and remain hypotheses; the compiler
and object oracle decide whether the layout is useful.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from eval import abi_leaf_pilot as leaf_pilot
from solver import refine, workspace


PICKUP_PRELUDE = """
/* Binary-layout hypothesis; names are m2c labels, offsets are target facts. */
struct RacePickupActor {
    u8 pad00[0x1C];
    Vec3i pos;
    u8 pad28[0x38];
    s32 velY;
    u8 pad64[0x20];
    s16 rotation;
    s16 variant;
};
extern u8 gRaceUpdatePaused;
extern u8 gTrainingCourseLesson;
extern u8 gItemEffectRollTable[1][0x40];
extern u8 gActionEffectRollTable[1][0x40];
"""


def inject_prelude(source: str, prelude: str = PICKUP_PRELUDE) -> str:
    function = re.search(r"(?m)^void\s+updateRacePickupIdle\s*\(", source)
    if not function:
        raise ValueError("parent definition missing from preflight source")
    return source[:function.start()] + prelude + "\n" + source[function.start():]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    if "configuration" in baseline:
        config = baseline["configuration"]
    else:
        original_path = Path(str(baseline["baseline_receipt"]))
        if not original_path.is_absolute():
            original_path = args.baseline.parent.parent / original_path
            if not original_path.exists():
                original_path = Path(str(baseline["baseline_receipt"]))
        original = json.loads(original_path.read_text(encoding="utf-8"))
        config = original["configuration"]
    parent = str(config["transfer_parent"])
    child = str(config["transfer_child"])
    parent_source = str(baseline["parent_result"]["best_source"])
    candidate = inject_prelude(parent_source)
    run_id = f"parent-struct-annotation-{int(time.time())}"

    repo = args.repo.expanduser().resolve()
    conn = sqlite3.connect(args.db.expanduser().resolve(), timeout=120)
    conn.execute("pragma busy_timeout = 120000")
    refine.ensure_schema(conn)
    ws = workspace.bootstrap(repo, parent)
    attempt = workspace.score(
        ws, repo, f"{run_id}_{parent}", candidate, conn=conn, func=parent,
        strategy="binary-struct-annotation", run_id=run_id,
        extra={"generation_tokens": 0, "target_source_read": False,
               "annotation_kind": "binary_offset_width_hypothesis"})

    heldout = leaf_pilot.heldout_names(
        [Path(path) for path in config.get("heldout_sets", [])])
    inferred_transfer = []
    if attempt.compiled and not attempt.exact:
        child_ws = workspace.bootstrap(repo, child)
        child_asm = workspace.target_asm(child_ws, child)
        inferred_transfer = leaf_pilot.measure_inferred_contract_transfer(
            repo, conn, [{"function": child,
                          "signals": leaf_pilot.abi_signals(child_asm)}],
            heldout, run_id=run_id, max_parents_per_leaf=1)

    variants = [variant for transfer in inferred_transfer
                for caller in transfer.get("callers", [])
                for variant in caller.get("variants", [])]
    receipt = {
        "schema_version": 1,
        "kind": "parent_struct_annotation_pilot",
        "run_id": run_id,
        "baseline_receipt": str(args.baseline),
        "pre_registration": {
            "prediction": (
                "binary-derived struct/global prelude makes the unchanged "
                "parent draft compile and enables the child ABI shadow test"),
            "generation_budget": 0,
            "target_source_read": False,
        },
        "annotation": {
            "semantic_names_are_hypotheses": True,
            "struct": "RacePickupActor",
            "target_offsets": {
                "pos": {"offset": 0x1C, "width": 12},
                "velY": {"offset": 0x60, "width": 4},
                "rotation": {"offset": 0x84, "width": 2},
                "variant": {"offset": 0x86, "width": 2},
            },
            "target_globals": {
                "gRaceUpdatePaused": {"load": "lbu", "width": 1},
                "gTrainingCourseLesson": {"load": "lbu", "width": 1},
                "gItemEffectRollTable": {"load": "lbu", "width": 1},
                "gActionEffectRollTable": {"load": "lbu", "width": 1},
            },
            "prelude": PICKUP_PRELUDE,
        },
        "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "candidate_source": candidate,
        "parent_result": {
            "function": parent, "compiled": attempt.compiled,
            "exact": attempt.exact, "score": attempt.score,
            "compiler_error": attempt.compiler_stderr[:3000],
            "diff": attempt.diff[:6000],
        },
        "inferred_transfer": inferred_transfer,
        "assessment": {
            "parent_baseline_compiled": attempt.compiled,
            "parent_baseline_exact": attempt.exact,
            "parent_baseline_score": attempt.score,
            "inferred_variants_compiled": sum(
                bool(variant.get("compiled")) for variant in variants),
            "inferred_assembly_deltas": sum(
                bool(variant.get("assembly_changed")) for variant in variants),
            "inferred_score_improvements": sum(
                float(variant.get("score_delta") or 0) > 0
                for variant in variants),
            "inferred_exact_promotions": sum(
                bool(variant.get("exact")) for variant in variants),
            "charged_generation_tokens": 0,
        },
        "completed_at": int(time.time()),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    conn.close()
    print(json.dumps(receipt["assessment"], indent=2))
    print(f"receipt: {args.out}")


if __name__ == "__main__":
    main()
