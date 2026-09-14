"""Does each rewrite generator work anywhere but the function it was built from?

THE PROBLEM THIS EXISTS FOR
    Every generator in solver/rewrites.py was written by reading two or three
    specific residuals and encoding what they showed. immediate_rewrites came
    from one function, argswap from one, per_object_layout from three,
    compare_swap from two, statement_order from one. That is the solver being
    fitted to the dev set BY HAND, and nothing has ever measured whether a
    generator fires usefully on a function it was not derived from.

    Until that is measured, "the generators composed inside the search and
    closed two functions" could be memorisation rather than capability, and so
    could every other number here. The 49-function held-out split in
    eval/sets/sbk1_v3.json has never been run, so there is no independent
    check either.

WHAT IT MEASURES, IN TWO TIERS
    FIRES   the generator proposes at least one rewrite. Free -- it needs the
            baseline residual, which is one compile per function regardless.
    HELPS   at least one of its proposals compiles AND lowers the fault count.
            Costs one compile per proposal, so it is budgeted.

    A generator that FIRES broadly but HELPS only on its motivating function
    is pattern-matching the shape without capturing the cause. A generator
    that fires on one function only is memorisation outright.

WHAT IT IS NOT
    Not a held-out evaluation. Every function here is one the solver has
    already seen. This measures breadth ACROSS the dev set, which is a lower
    bar than generalisation and the most that can be checked without spending
    the held-out split.

    python3 -m eval.generalization --budget 300
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

from eval import faultsearch, matched as matched_mod
from solver import rewrites, signals, workspace

# The generator, and the function whose residual it was written from, so the
# report can separate "works elsewhere" from "works where it was born".
GENERATORS = [
    ("layout", rewrites.layout_rewrites, "updateRaceSetupFourPlayerOption"),
    ("per_object_layout", rewrites.per_object_layout_rewrites,
     "updateCourseSelectCourseDescription"),
    ("pointer_table_deref", rewrites.pointer_table_deref_rewrites,
     "initRaceMotionModelParts"),
    ("reloc_padding", rewrites.reloc_padding_rewrites,
     "updateRaceSplitscreenSelectPlayerCountIcons"),
    ("reloc_symbol", rewrites.reloc_symbol_rewrites,
     "initControllerPakRaceRecordSaveFlow"),
    ("drop_mask", rewrites.drop_mask_rewrites, "requestRumbleMotorStart"),
    ("loop_shape", rewrites.loop_shape_rewrites, ""),
    ("frame_padding", rewrites.frame_padding_rewrites, ""),
    ("statement_order", rewrites.statement_order_rewrites,
     "renderRaceUiSingleTrailEffect"),
    ("compare_swap", rewrites.compare_swap_rewrites, "calculateRaceTimerDelta"),
    ("inline_temporary", rewrites.inline_temporary_rewrites, ""),
    ("branch_sentinel", rewrites.branch_sentinel_rewrites,
     "hasPendingRaceReplayCourseGridEntry"),
    ("signed_compare", rewrites.signed_compare_rewrites,
     "updateEndingSlashSlideRightToMarker"),
    ("immediate", rewrites.immediate_rewrites,
     "updateRaceSplitscreenSelectPlayerCountIcons"),
    ("argswap", rewrites.argswap_rewrites, "updateEndingLindaExitUntilPhase3C"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--budget", type=int, default=300,
                    help="compiles spent testing whether proposals HELP")
    ap.add_argument("--max-per-generator", type=int, default=4,
                    help="proposals tested per generator per function")
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    done = matched_mod.already_matched(conn, repo)
    rows = conn.execute(
        "select f.name, max(a.score), a.source_code from attempts a"
        " join functions f on f.addr = a.func_addr"
        " where a.compiled = 1 and a.source_code is not null"
        " group by f.name").fetchall()
    targets = [(n, s, src) for n, s, src in rows if n not in done]
    targets.sort(key=lambda r: -r[1])
    print(f"{len(targets)} unmatched functions with a compiling candidate\n")

    fires: dict[str, set] = defaultdict(set)
    helps: dict[str, set] = defaultdict(set)
    proposals: dict[str, int] = defaultdict(int)
    used = 0

    for name, _score, src in targets:
        try:
            ws = workspace.bootstrap(repo, name)
            base = workspace.score(ws, repo, name, src)
        except Exception:                          # noqa: BLE001
            continue
        if not base.compiled or not base.diff:
            continue
        base_faults = faultsearch.faults_of(
            signals.analyse(base.diff, base.score), base.diff)

        for label, fn, _origin in GENERATORS:
            try:
                rws = fn(src, base.diff)
            except Exception as exc:               # noqa: BLE001
                print(f"  {label} raised on {name}: {str(exc)[:50]}")
                continue
            if not rws:
                continue
            fires[label].add(name)
            proposals[label] += len(rws)
            if used >= args.budget:
                continue
            for rw in rws[:args.max_per_generator]:
                if used >= args.budget:
                    break
                new = rw(src)
                if new == src:
                    continue
                att = workspace.score(ws, repo, name, new)
                used += 1
                if not att.compiled:
                    continue
                if att.exact:
                    helps[label].add(name)
                    print(f"  *** {label} reached EXACT on {name} ***")
                    break
                f = faultsearch.faults_of(
                    signals.analyse(att.diff, att.score, att.exact), att.diff)
                if f < base_faults:
                    helps[label].add(name)
                    break

    hdr = (f"{'generator':<22}{'fires on':>9}{'helps':>7}{'proposals':>11}"
           f"  {'helps beyond its origin?':<26}")
    print("\n" + hdr)
    print("-" * len(hdr))
    for label, _fn, origin in GENERATORS:
        f_set, h_set = fires[label], helps[label]
        beyond = sorted(h_set - {origin})
        if not f_set:
            verdict = "never fires"
        elif not h_set:
            verdict = "fires but never helps"
        elif not beyond:
            verdict = "ONLY its origin function"
        else:
            verdict = f"yes -- {len(beyond)} other function(s)"
        print(f"{label:<22}{len(f_set):>9}{len(h_set):>7}"
              f"{proposals[label]:>11}  {verdict:<26}")

    print(f"\ncompiles spent on HELP testing: {used} of {args.budget}")
    print("FIRES is free; HELPS is budgeted, so a generator late in the list "
          "may be under-tested.\nRead 'never fires' as a finding to explain, "
          "not a null to accept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
