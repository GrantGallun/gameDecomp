"""Compose independently-scored rewrites. The single-step loop cannot.

eval/diffloop.py applies one rewrite per round and stops when a step fails to
improve, so a fix needing two simultaneous changes is unreachable by
construction. Both matches recovered after an external review were exactly
that shape:

    loop bound 4 -> 5           99.962      neither alone
    8 bytes before menuState    99.962      reaches exact
    BOTH                        EXACT

Each rewrite moves the score by four hundredths of a point. Greedy hill
climbing cannot see either as progress, and this project recorded that finding
months of work ago -- "a function at 99.6% has SEVERAL small interacting
errors" -- and kept building single-step repairs anyway.

METHOD
    score every proposed rewrite alone, then try PAIRS. A rewrite is kept for
    composition if it does not make things worse: the two matches needed
    rewrites worth +0.04 each, which is indistinguishable from noise, so
    filtering on "improves" would have discarded them.

    The oracle verifies every combination. Nothing here decides a match.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import rewrites, workspace


def search(repo: Path, name: str, src: str, ws: Path, conn=None,
           max_pairs: int = 250, verbose: bool = True):
    """Return (best_attempt, best_source, log)."""
    log: list[str] = []
    base = workspace.score(ws, repo, name, src)
    if not base.compiled:
        return base, src, ["baseline did not compile"]

    proposals = rewrites.propose(src, base.diff)
    if not proposals:
        return base, src, ["no applicable rewrite"]

    singles = []
    best_att, best_src = base, src
    for rw in proposals:
        cand = rw(src)
        if cand == src:
            continue
        att = workspace.score(ws, repo, name, cand, conn=conn, func=name,
                              strategy="compose", run_id="compose")
        if not att.compiled:
            continue
        if att.exact:
            return att, cand, log + [f"{rw.label}: EXACT alone"]
        # EVERY compiling rewrite is kept, including ones that score WORSE.
        # Filtering on single-rewrite score contradicts the whole premise: on
        # updateEndingLindaExitUntilPhase3C the layout repair scores 99.205
        # against a 99.545 baseline -- it shifts fields that are still being
        # read through swapped arguments -- and yet it is half of the pair that
        # reaches exact. A rewrite that hurts alone can be essential in
        # combination, which is what "several small interacting errors" means.
        # Score orders the search; it does not admit or reject.
        singles.append((rw, cand, att.score))
        if att.score > best_att.score:
            best_att, best_src = att, cand

    singles.sort(key=lambda t: -t[2])
    log.append(f"{len(proposals)} proposed, {len(singles)} compiling")
    if verbose:
        for rw, _c, sc in singles[:8]:
            print(f"      {rw.label[:44]:44} {sc:8.3f}"
                  f" ({sc - base.score:+.3f})", flush=True)

    # CROSS-KIND PAIRS FIRST. Both known matches are layout + something else
    # (padding + a constant, padding + an argument swap), and sorting purely by
    # single-rewrite score let same-kind pairs crowd the winning combination
    # past the cap: the Linda case spent all 60 tries on pairs of argument
    # swaps and never reached layout + swap.
    combos = sorted(
        itertools.combinations(singles, 2),
        key=lambda pair: (pair[0][0].kind == pair[1][0].kind,
                          -(pair[0][2] + pair[1][2])))

    # Composing two PRECOMPUTED rewrites is not enough, and the reason is
    # instructive. On updateEndingLindaExitUntilPhase3C the argument swap makes
    # `lh a2,0x26` and `lh a2,0x24` align on their normalised form, so
    # diffrepair reads a bogus offset constraint 0x24 -> 0x26 out of what is
    # really a register swap. The layout rewrite built from that stale diff is
    # wrong, and applying the swap afterwards does not undo it.
    #
    # So re-derive after every application: apply one rewrite, recompile, and
    # propose again from the NEW residual. That is the counterexample-guided
    # loop, and it is what makes the second rewrite correct rather than stale.
    tried = 0
    for rw, cand, _sc in singles:
        if tried >= max_pairs:
            break
        first = workspace.score(ws, repo, name, cand)
        if not first.compiled:
            continue
        for rw2 in rewrites.propose(cand, first.diff):
            if tried >= max_pairs:
                break
            cand2 = rw2(cand)
            if cand2 == cand:
                continue
            tried += 1
            att = workspace.score(ws, repo, name, cand2, conn=conn, func=name,
                                  strategy="compose-pair", run_id="compose")
            if not att.compiled:
                continue
            if att.exact:
                log.append(f"PAIR EXACT: {rw.label} then {rw2.label}")
                return att, cand2, log
            if att.score > best_att.score:
                best_att, best_src = att, cand2
                log.append(f"pair improved: {rw.label} then {rw2.label} -> "
                           f"{att.score:.3f}")
    log.append(f"{tried} re-derived pairs tried")
    return best_att, best_src, log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--floor", type=float, default=60.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()))
    done = matched_mod.already_matched(conn)

    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
    else:
        names = [r[0] for r in conn.execute(
            "select f.name, max(a.score) as best from functions f"
            " join attempts a on a.func_addr = f.addr"
            " where a.compiled = 1 group by f.addr having best >= ?"
            " order by best desc", (args.floor,)).fetchall()
            if r[0] not in done]

    print(f"{len(names)} functions\n")
    wins, improved = [], []
    for name in names:
        # The best NON-exact candidate. Selecting the best outright picked a
        # source that was already byte-exact once receipts existed, so the
        # search "found" matches it had been handed -- a vacuous test. If a
        # function already has an exact candidate there is nothing to compose.
        row = conn.execute(
            "select a.score, a.source_code from attempts a"
            " join functions f on f.addr = a.func_addr"
            " where f.name = ? and a.compiled = 1 and a.source_code is not null"
            "   and a.score < 100 order by a.score desc limit 1",
            (name,)).fetchone()
        if not row:
            continue
        start, src = row
        ws = workspace.bootstrap(repo, name)
        print(f"{name[:48]:48} start {start:.3f}", flush=True)
        att, best_src, log = search(repo, name, src, ws, conn=conn)
        for line in log:
            if "EXACT" in line or "improved" in line:
                print(f"      {line}", flush=True)
        if att.exact:
            wins.append((name, start))
            out = Path("matched_recovered") / f"{name}.c"
            out.parent.mkdir(exist_ok=True)
            out.write_text(best_src)
            print(f"      -> BYTE-EXACT, wrote {out}", flush=True)
        elif att.score > start + 0.0005:
            improved.append((name, start, att.score))

    print(f"\n{'=' * 66}")
    print(f"NEW BYTE-EXACT MATCHES: {len(wins)}")
    for n, s in wins:
        print(f"   {n}   {s:.3f} -> EXACT")
    print(f"improved, not matched: {len(improved)}")
    for n, a, b in improved:
        print(f"   {n}   {a:.3f} -> {b:.3f}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"matches": [{"function": n, "was": s} for n, s in wins],
             "improved": [{"function": n, "was": a, "now": b}
                          for n, a, b in improved]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
