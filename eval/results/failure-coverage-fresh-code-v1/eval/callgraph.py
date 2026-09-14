"""The call graph the KB already has, and the work ordering it implies.

NOT A MISSING CAPABILITY -- AN UNUSED ONE. The evidence tier holds 9,854 call
rows, 9,761 with a resolved target, spanning 1,819 callers and 657 callees, and
`functions.is_leaf` is populated for all 2,113. Nothing reads any of it:
`func_deps` has zero rows, and the only modules that mention call evidence are
kb/tms.py and eval/poison_test.py. Meanwhile the solver orders its work by
descending byte score, which has nothing to do with dependencies.

That is the same shape as the other findings today -- the data was there and
had no consumer -- and it is worth more than the others because call edges are
keyed by ADDRESS. Every cross-function link built so far keys on a struct name
invented by the model in candidate source, which is fragile by construction.
An edge from the binary is not.

THREE THINGS IT BUYS, in descending order of how checkable they are:

  PROTOTYPES  If A calls B and B is matched, B's real signature is known, so
              A's call site must agree with it. That is a ground-truth
              constraint on A derived without attempting A at all.
  ORDERING    Solve callees before callers -- the classic decomp workflow --
              so a caller is attempted only once its callees' prototypes are
              settled.
  SIBLINGS    Functions sharing callees plausibly share types. The flywheel
              currently ranks siblings by assembly similarity alone and finds
              usable coverage for 1 of 34 hard functions; this is a different
              signal, not a better threshold on the same one.

This module reports; it changes no behaviour.

    python3 -m eval.callgraph
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

from eval import matched as matched_mod


def edges(conn) -> tuple[dict, dict]:
    """(callees_of, callers_of) by function NAME, resolved through addresses."""
    callees: dict = defaultdict(set)
    callers: dict = defaultdict(set)
    for caller, callee in conn.execute(
            "select cf.name, tf.name from evidence e"
            " join functions cf on cf.addr = e.func_addr"
            " join functions tf on tf.addr = e.target_addr"
            " where e.kind = 'call' and e.target_addr is not null"):
        if caller == callee:
            continue                       # direct recursion is not a dependency
        callees[caller].add(callee)
        callers[callee].add(caller)
    return callees, callers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--show", type=int, default=15)
    ap.add_argument("--out", type=Path,
                    help="write a run_set-compatible queue of never-attempted "
                         "high-leverage callees, highest leverage first")
    ap.add_argument("--max-insns", type=int, default=60,
                    help="cap the emitted queue at this size, so the first "
                         "pass takes the cheap unblockers")
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    done = matched_mod.already_matched(conn, repo)
    callees, callers = edges(conn)

    attempted = {n for (n,) in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr = a.func_addr")}
    unmatched = sorted(attempted - set(done))

    ready, blocked = [], []
    for name in unmatched:
        cs = callees.get(name, set())
        pending = {c for c in cs if c not in done}
        (ready if not pending else blocked).append((name, len(cs), pending))

    print(f"call graph: {sum(len(v) for v in callees.values())} edges, "
          f"{len(callees)} callers, {len(callers)} callees\n")
    print(f"attempted-but-unmatched functions: {len(unmatched)}")
    print(f"  every callee already matched (READY) : {len(ready)}")
    print(f"  still waiting on a callee (BLOCKED)  : {len(blocked)}\n")

    print(f"READY -- callees settled, so their prototypes are known:")
    for name, n, _p in sorted(ready, key=lambda r: -r[1])[:args.show]:
        print(f"   {name:<48} {n} callee(s)")

    print(f"\nBLOCKED -- and what each is waiting on:")
    for name, n, pending in sorted(blocked, key=lambda r: len(r[2]))[:args.show]:
        wait = ", ".join(sorted(pending)[:3])
        more = "" if len(pending) <= 3 else f" +{len(pending)-3}"
        print(f"   {name:<44} waits on {len(pending)}: {wait}{more}")

    # Which matched functions would unblock the most work if used as context.
    leverage: dict = defaultdict(int)
    for name, _n, pending in blocked:
        for p in pending:
            leverage[p] += 1
    if leverage:
        print(f"\nHIGHEST-LEVERAGE UNSOLVED CALLEES "
              f"(solving one unblocks several callers):")
        for callee, n in sorted(leverage.items(), key=lambda kv: -kv[1])[:args.show]:
            mark = "" if callee in attempted else "   [never attempted]"
            print(f"   {callee:<48} unblocks {n}{mark}")

    if args.out:
        _write_queue(conn, args.out, leverage, attempted, args.max_insns)
    return 0


def _write_queue(conn, out: Path, leverage: dict, attempted: set,
                 max_insns: int) -> None:
    """Emit the unblocking work as a set file eval.run_set already understands.

    Ordered by how many callers each one frees, then by size, because the
    cheapest unblocker should go first: the two solved by hand while writing
    this were 8 and 2 instructions and between them 15 callers were waiting.
    Never-attempted only -- a function with history is already in some queue.
    """
    sizes = {n: (s, i, leaf) for n, s, i, leaf in conn.execute(
        "select name, size, insn_count, is_leaf from functions")}
    rows = []
    for callee, unblocks in leverage.items():
        if callee in attempted or callee not in sizes:
            continue
        size, insn, leaf = sizes[callee]
        n = insn or (size or 0) // 4
        if not n or n > max_insns:
            continue
        rows.append({
            "function": callee,
            "tier": ("tiny" if n < 20 else "small" if n < 60 else "medium"),
            "leaf": bool(leaf),
            "insns": n,
            "unblocks": unblocks,
        })
    rows.sort(key=lambda r: (-r["unblocks"], r["insns"]))
    payload = {
        "derived_from": "eval.callgraph",
        "note": ("Never-attempted callees ordered by how many unmatched "
                 "callers they block, then by size. The work queue has always "
                 "been ordered by byte score, which is dependency-blind: "
                 "getRelocatableHeapBlockBase is 8 instructions, blocks 15 "
                 "callers, had never been attempted, and matched on the first "
                 "structural guess."),
        "selection": {"never_attempted": True, "max_insns": max_insns},
        "dev": rows,
        "heldout": [],
    }
    import json
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("")
    print(f"wrote {len(rows)} function(s) to {out}")


if __name__ == "__main__":
    raise SystemExit(main())
