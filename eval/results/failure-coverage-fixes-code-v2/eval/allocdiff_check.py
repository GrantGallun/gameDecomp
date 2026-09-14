"""Validate the web model, THEN read its prescription. In that order.

Two checks, because they fail differently:

  CONCORDANCE  On the TARGET stream -- real IDO output -- a web the model
               ranks earlier should hold a lower-indexed colour. This is the
               model against reality and it must be measured on functions we
               have already matched, where the target is known-good code.

  ANCHOR       On an unmatched function, name the earliest mis-coloured web
               and what would have to change about it.

If concordance is poor the prescription is noise, so it is printed first and
the prescription is labelled with it.

    python3 -m eval.allocdiff_check --only renderRaceUiSingleTrailEffect
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import allocdiff, diffrepair, uopt, workspace


def concordance_on_matched(repo: Path, conn, limit: int) -> None:
    done = sorted(matched_mod.already_matched(conn, repo))
    tot_c = tot_n = 0
    shown = 0
    print("CONCORDANCE of the save model against real IDO output")
    for name in done:
        ws = repo / "nonmatchings" / name
        tgt = ws / "target_object_dump_normalized.s"
        if not tgt.exists():
            continue
        asm = tgt.read_text(errors="replace")
        webs = uopt.webs(asm)
        if len(webs) < 3:
            continue
        uopt.mark_precolored(webs, asm)
        c, n = uopt.rank_agreement(webs)
        if not n:
            continue
        tot_c += c
        tot_n += n
        if shown < limit:
            print(f"  {name:<46} {c:4}/{n:<4} {100*c/n:5.1f}%")
            shown += 1
    if tot_n:
        print(f"  {'TOTAL':<46} {tot_c:4}/{tot_n:<4} {100*tot_c/tot_n:5.1f}%")
    else:
        print("  no comparable pairs -- the model cannot be validated")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--show", type=int, default=8)
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    concordance_on_matched(repo, conn, args.show)

    if not args.only:
        return 0
    print("\nANCHOR web on the requested functions")
    for name in args.only:
        src = conn.execute(
            "select a.source_code from attempts a join functions f"
            " on f.addr = a.func_addr where f.name = ? and a.compiled = 1"
            " and a.source_code is not null order by a.score desc limit 1",
            (name,)).fetchone()[0]
        ws = workspace.bootstrap(repo, name)
        att = workspace.score(ws, repo, name, src)
        p = allocdiff.prescribe(att.diff or "")
        print(f"\n{name}  score={att.score:.3f}")
        if not p["applicable"]:
            print(f"   not applicable: {p['reason']}")
            continue
        if p.get("anchor") is None:
            print("   no mis-coloured web: the residual is not allocation")
            continue
        print(f"   mis-coloured webs: {p['total_mismatched_webs']}")
        print(f"   ANCHOR at stream line {p['line']}: target wants "
              f"{p['target_reg']}, we produced {p['cand_reg']}")
        print(f"   our web: n={p['occurrences']} nocs={p['nocs']} "
              f"save={p['save']:.2f}   must rank {p['want']}")
        if p["options"]:
            print("   occurrence counts that move save the right way "
                  "(nearest first):")
            for n, save in p["options"]:
                delta = n - p["occurrences"]
                grade = allocdiff.evidence_grade(p["anchor"].cand_web, n)
                print(f"      n={n:3} ({delta:+d})  nocs="
                      f"{allocdiff.nocs_for(n)}  save={save:.2f}   {grade}")
            print("   instructions referencing this web:")
            for t, c in allocdiff.web_instructions(att.diff or "", p["line"]):
                mark = " " if t == c else "*"
                print(f"     {mark} -{t:<34} +{c}")
        else:
            print("   no occurrence count changes save the right way; the "
                  "difference is in a COMPETING web, not this one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
