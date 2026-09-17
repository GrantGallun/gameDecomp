"""Generate the project status from ground truth, so it cannot drift.

CLAUDE.md said "Design complete. No implementation yet." for weeks while 65
tests passed and 33 functions matched. An external review caught it. CLAUDE.md
is auto-loaded into every session, so a stale status makes every future agent
reason from a false premise -- the most expensive kind of documentation bug.

Numbers come from the KB, the filesystem and git. Nothing here is maintained by
hand.

    python3 -m eval.status            # markdown block for CLAUDE.md
    python3 -m eval.status --check    # non-zero if CLAUDE.md disagrees

WHY THIS NUMBER IS NOT THE CAMPAIGN'S NUMBER (resolved 2026-09-17)
------------------------------------------------------------------
This reports the RESEARCH knowledge base (`kb-sbk1.sqlite`, 502 attempted). The live campaign reports
its own, much larger figure from `eval/results/resume-pipeline-<date>/campaign.json`, and the two read
as a contradiction -- README says "909 object-exact (44.3%) of 2,051", health.json said 937, and this
says ~214. They are not the same quantity, and the campaign's own checkpoint says so outright:

    summary.cohort_functions                   2051
    summary.object_exact_or_integrated          958
    fast_metrics.repair_yield.totals.exact_functions_gained   262
    fast_metrics.repair_yield.totals.exact_functions_lost       0

`object_exact_or_integrated` counts NODES WHOSE STATUS IS object_exact -- and the cohort was seeded
from the reference decompilation, so most of that total was already exact before the pipeline ran.
`repair_yield` counts what the controller actually produced: **262 functions gained, 0 lost**.

    958 nodes object_exact_or_integrated
  - 262 gained by the campaign's own repair yield
  = 696 that arrived already exact

So the 44% headline is ~69% pre-existing state, and the campaign's produced capability is 262 of 2,051
(12.8%), against this module's SOLVED for its own smaller population. Quote neither as the other.
The 262 is a distinct-function count from the controller's own ledger and is the closest thing the
campaign has to a capability number; `exact_items` (286) is a WORK-ITEM count from
`fast_campaign.py:389` and must not be substituted for it.

SECOND KNOWN GAP: THE TIER RULE DOES NOT FOLLOW LINEAGE (found 2026-09-17)
-------------------------------------------------------------------------
`recovered` and `header_assisted` are keyed on the EXACT ATTEMPT's own strategy string, so a
deterministic edit applied on top of a reference-derived candidate is booked as SOLVED even though the
candidate is the reference's own answer. Both matches closed on 2026-09-17 landed in that gap:
`func_80063A9C` (candidate origin `dag-pipeline-census-root`, genuinely capability) and
`updateRaceGameplayFlow` (candidate origin `authorized-target-history-recovery`, recovery-derived) are
both counted as SOLVED, because the winning attempts are `do-restore:...` and match no recovery
pattern. `campaign-intake:explicit-historical-seed`, which is reference-derived, matches none of the
three patterns at all. A first count of the exposure, not yet deduplicated to functions: 66 exact
attempts have a recovery-strategy parent while their own strategy carries no recovery marker.

Not changed here on purpose, for the same reason as the include-based gap above: tightening it would
LOWER the reported SOLVED count, which reads as a ratchet violation. Whether that is a correction or a
regression is the operator's call. Until it is decided, quote the SOLVED number knowing that a
deterministic edit on a recovered source counts as one, and read the `recovered` sub-row as a floor
rather than a total.
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
import subprocess
from pathlib import Path

from eval import matched as matched_mod
from kb import attempts as attempt_receipts


def counts(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    q = conn.execute
    exact_db = matched_mod.matched_in_db(conn)
    # Candidates verified byte-exact but never logged, e.g. recovered from
    # permuter output. Counting only the DB under-reports; counting only files
    # over-reports. The union is the honest figure -- getting this wrong is how
    # 33 was reported as 34.
    on_disk = {os.path.basename(p)[:-2]
               for p in glob.glob("matched_recovered/*.c")}
    # SOLVED is not the same as RECOVERED, and the headline conflated them.
    # tools/score_repo_function.py deliberately reads a function's source from
    # the target repository -- it says so, it is gated behind an explicit flag,
    # and it records its provenance honestly. What it is not is a solve: those
    # functions are the reference decomp's own answers, oracle-verified and
    # copied in. Legitimate for seeding the sibling pool, fatal to a headline
    # that reads "functions byte-exact" and is quoted as a capability number.
    # CLAUDE.md is explicit that ground truth is for CHECKING, never feeding,
    # so the two are counted apart and both are printed.
    recovered = {
        name for (name,) in q(
            "select distinct f.name from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where a.exact = 1 and ("
            "   a.strategy like '%history-recovery%'"
            "   or a.strategy like '%historical-provenance%'"
            "   or a.strategy like '%symbol-restoration%')")
    }
    # HEADER-ASSISTED is a third thing, and folding it into either of the
    # other two misstates what happened. solver/project_headers.py adds a
    # reconstructed include/game/** header to the draft, which supplies the
    # decomp team's own prototype -- parameter names included -- and the full
    # struct layout. getSchedulerAudioTaskQueue matched as
    # `return &scheduler->messageQueue;` against a header that declares
    # `OSMesgQueue *getSchedulerAudioTaskQueue(SchedulerState *scheduler);`
    # and lays out SchedulerState in full. That is not copying the body, so it
    # is not RECOVERED; it is also not a solve the pipeline could have reached
    # on binary evidence, so it is not SOLVED. CLAUDE.md's contamination line
    # puts include/PR/** (public SDK) on one side and include/game/** on the
    # other. Counted apart, printed, and excluded from the capability number.
    #
    # KNOWN GAP, stated rather than silently corrected (2026-09-16). This keys ONLY on the strategy
    # string, so it sees header assistance arriving through `project_headers.prompt_context` and is
    # blind to a candidate that reaches the same reconstructed headers by `#include`-ing them -- the
    # project compiles against `sbk1/include/`, so that route is open to any profile.
    #
    # Found on `updateEndingTommyWaitThenFinalPhase`, finished by a deterministic `decl_order` edit
    # from a `campaign-compile-recovery:opaque-parameter-layout` candidate. Its source includes five
    # `game/**` headers, yet it DECLARES ITS OWN struct with binary-derived offsets
    # (`char pad00[0x2a]; u16 unk2A;`, comment: "Offsets are binary facts; member names are m2c labels
    # and therefore hypotheses. Generated by solver/typedecl.py"). So the layout is binary-derived and
    # the includes look vestigial -- but "looks vestigial" is a judgement, not a measurement.
    #
    # Not changed here on purpose: an include-based test would reclassify existing SOLVED matches and
    # LOWER the SOLVED count, which reads as a ratchet violation. Whether that is a correction or a
    # regression is the operator's call, and it needs the check run across the whole matched set
    # rather than applied to one new match.
    header_assisted = {
        name for (name,) in q(
            "select distinct f.name from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where a.exact = 1 and a.strategy like '%project-header%'")
    } - recovered
    every = exact_db | on_disk
    return {
        "exact": len(every),
        "solved": len(every - recovered - header_assisted),
        "header_assisted": len(every & header_assisted),
        "recovered": len(every & recovered),
        "exact_db_only": len(exact_db),
        "exact_disk_only": len(on_disk - exact_db),
        "historical_exact_unknown": attempt_receipts.unknown_exact_count(conn),
        "attempted": q("select count(distinct func_addr) from attempts").fetchone()[0],
        "attempts": q("select count(*) from attempts").fetchone()[0],
        "evidence": q("select count(*) from evidence").fetchone()[0],
        "inference": q("select count(*) from inference").fetchone()[0],
    }


def tests() -> int:
    out = subprocess.run(["python3", "-m", "pytest", "tests/", "-q",
                          "--collect-only"], capture_output=True, text=True)
    for line in reversed(out.stdout.splitlines()):
        if "test" in line and "collected" in line:
            for tok in line.split():
                if tok.isdigit():
                    return int(tok)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path,
                    default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if CLAUDE.md's match count is stale")
    args = ap.parse_args()

    c = counts(args.db)
    n_tests = tests()

    print(f"| functions byte-exact | **{c['exact']}** of {c['attempted']} attempted |")
    print(f"| — of which SOLVED | **{c['solved']}** |")
    print(f"| — of which header-assisted (reconstructed include/game) "
          f"| {c['header_assisted']} |")
    print(f"| — of which recovered from target source | {c['recovered']} |")
    print(f"| attempts logged | {c['attempts']:,} |")
    print(f"| evidence rows | {c['evidence']:,} |")
    print(f"| **inference rows** | **{c['inference']}** |")
    print(f"| tests | {n_tests} |")
    if c["exact_disk_only"]:
        print(f"\n({c['exact_disk_only']} verified match(es) exist only as files "
              f"in matched_recovered/ and are absent from the attempts table --"
              f" they were produced by a harness that did not log.)")
    if c["historical_exact_unknown"]:
        print(f"\n({c['historical_exact_unknown']:,} historical compiled attempt(s) "
              "predate persisted exact verdicts. They are treated as unknown, "
              "never inferred from score.)")

    if args.check:
        md = Path("CLAUDE.md").read_text(encoding="utf-8", errors="replace")
        if f"**{c['exact']}** of {c['attempted']} attempted" not in md:
            print(f"\nSTALE: CLAUDE.md does not state "
                  f"{c['exact']} of {c['attempted']}. Regenerate it.")
            return 1
        print("\nCLAUDE.md status is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
