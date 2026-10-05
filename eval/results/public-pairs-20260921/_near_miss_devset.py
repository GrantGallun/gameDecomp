"""Turn the real target's near misses into a runnable panel for the search that closed one today.

A near miss here is a function of the ACTUAL game (Snowboard Kids 1) in the project's own KB that has at
least one attempt which compiled and none which was exact -- so the oracle can score it, the candidate is
real C, and `regalloc-search` has something to act on. Today's development panel closed 1 of 17 such
states; the KB has hundreds.

WHAT THIS REFUSES TO DO, and why each refusal matters:

  * a function already `matched` in the KB's own `functions.state`, or whose name is defined in the
    reference decomp's `src/**`, is EXCLUDED. `attempt.exact` is a lower bound -- the audit found 12
    functions with a passing ROM-backed extent certificate whose attempts never set it, all already
    matched -- and reporting one of those as a new close would be a recount.
  * a near miss with no name is reported as unnameable rather than guessed at, because `build_context`
    takes a name and an address is not one.
  * the candidate is the KB's own best-scoring attempt for that function, copied by bytes and hashed, not
    regenerated: a panel measured on bytes must be built from those bytes.

Read-only with respect to the build. Run in WSL:
  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.public-pairs-20260921._near_miss_devset --limit 40
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path("/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def solved_names(source_root: Path) -> set:
    """Every function the reference decomp actually implements."""
    names = set()
    if not source_root.is_dir():
        return names
    for path in source_root.rglob("*.c"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names |= set(re.findall(r"(?m)^[A-Za-z_][\w \t*]*?\b(\w+)\s*\([^;{]*\)\s*\{", text))
    return names


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--out", type=Path, default=HERE / "near-miss-devset.json")
    ap.add_argument("--sources", type=Path, default=HERE / "near-miss-sources")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "select a.func_addr, f.name, f.state, f.size, max(a.score) best "
            "from attempts a join functions f on f.addr = a.func_addr "
            "where coalesce(a.compiled,0)=1 "
            "  and a.func_addr not in (select func_addr from attempts where coalesce(exact,0)=1) "
            "group by a.func_addr order by best desc").fetchall()
        candidates = []
        already, unnameable, no_source = [], [], []
        solved = solved_names(REPO / "src")
        for addr, name, state, size, best in rows:
            if not name:
                unnameable.append({"func_addr": addr, "best": best})
                continue
            if str(state) == "matched" or name in solved:
                already.append({"function": name, "state": state, "best": best})
                continue
            if best is None or float(best) < args.min_score:
                continue
            source = conn.execute(
                "select source_code, score, source_sha256 from attempts where func_addr=? "
                "and coalesce(compiled,0)=1 and source_code is not null "
                "order by score desc, id desc limit 1", (addr,)).fetchone()
            if not source or not source[0].strip():
                no_source.append({"function": name, "best": best})
                continue
            candidates.append({"func_addr": addr, "function": name, "state": state, "size": size,
                               "kb_best": best, "source": source[0], "kb_score": source[1],
                               "kb_source_sha256": source[2]})
    finally:
        conn.close()

    from eval.tool_agent_run import build_context

    args.sources.mkdir(parents=True, exist_ok=True)
    entries, mismatched, unavailable = [], [], []
    for record in candidates[: args.limit]:
        name, source = record["function"], record["source"]
        if record["kb_source_sha256"] and sha(source) != record["kb_source_sha256"]:
            mismatched.append({"function": name})
            continue
        context, why = build_context(REPO, name)
        if context is None:
            unavailable.append({"function": name, "reason": str(why)[:160]})
            continue
        path = args.sources / f"{name}.c"
        path.write_text(source, encoding="utf-8")
        if sha(path.read_text(encoding="utf-8")) != sha(source):
            mismatched.append({"function": name, "reason": "written bytes do not hash to the recorded"})
            continue
        entries.append({"function": name, "sha256": sha(source),
                        "baseline_draft_sha256": sha(context.candidate or ""),
                        "assistance": {"tier": "binary-only",
                                       "note": "the KB's own attempt; its strategy string is not a "
                                               "declaration source, so no assistance is claimed"},
                        "recorded": {"ido_compiled": True, "exact": False,
                                     "kb_best_score": record["kb_best"],
                                     "kb_attempt_score": record["kb_score"],
                                     "size": record["size"], "kb_state": record["state"]},
                        "lineage": {"source": "kb best-scoring compiled attempt",
                                    "func_addr": record["func_addr"]},
                        "training_eligible": False,
                        "training_exclusion": "development panel: measured on, never trained on"})
        print(json.dumps({"function": name, "kb_best": record["kb_best"]}), flush=True)

    payload = {"schema_version": 5, "kind": "near-miss-devset", "kb": str(KB),
               "selection": {"selected": len(entries), "near_misses": len(rows),
                             "excluded_already_solved": len(already),
                             "excluded_unnameable": len(unnameable),
                             "excluded_without_source": len(no_source),
                             "excluded_hash_mismatch": mismatched,
                             "excluded_no_context": unavailable,
                             "already_solved": already[:20]},
               "regime": ("REAL TARGET near misses (Snowboard Kids 1), from the project's own KB. The "
                          "panel is exposed development data; nothing here is a sealed evaluation and no "
                          "entry may be trained on."),
               "entries": entries}
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload["selection"].items() if k != "already_solved"},
                     indent=2)[:1200])
    print("written", args.out)
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
