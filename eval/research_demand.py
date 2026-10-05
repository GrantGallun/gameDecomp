"""What should be researched, decided by real failures rather than by the researcher's taste.

A queue of clusters over stored attempts, built from DETERMINISTIC features only: no model reads the
attempts, no model decides what is frequent. That is deliberate -- the researcher's job is to explain a
cluster, not to discover that it exists, and a feature extractor that calls a model cannot be replayed
or audited.

FEATURES, all computed from columns the attempt log already writes:
  error class      the compiler's own first diagnostic, with file/line stripped, so `cfe: Error:
                   candidate.c, line 4: Syntax Error` and its line-12 sibling are one feature.
  residual kind    `no-diff`, `registers-only` (the two sides differ only in register names -- the
                   residual is register allocation, not structure) or `structural`.
  size bucket      from the function table, so a cluster is not dominated by one giant function.
  compiled         whether the candidate even reached the oracle.

SPLIT DISCIPLINE. Clusters are built from TRAIN and DEV functions only. Held-out functions are removed
before anything is written, and the queue records only their COUNT, never their names: the researcher
reads this file, so writing a held-out name into it would be a leak with extra steps. `assert_no_
held_out` re-checks the written artifact rather than trusting the filter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = 1
LINE_NOISE = re.compile(r"[A-Za-z0-9_./\\-]*candidate\.c,?\s*line\s*\d+\s*:?", re.IGNORECASE)
REGISTER = re.compile(r"\b(?:[astv][0-9]|t[0-9]|ra|zero|at|gp|sp|fp|k[01]|r[0-9]{1,2})\b")


def normalize_error(stderr: str) -> str:
    """One stable feature from a compiler diagnostic, with the line number and file removed."""
    first = (stderr or "").strip().splitlines()[0] if (stderr or "").strip() else ""
    first = LINE_NOISE.sub("", first)
    first = re.sub(r"\s+", " ", first).strip()
    return first[:120] or "no-error-recorded"


def mask_registers(text: str) -> str:
    return REGISTER.sub("R", text)


def residual_kind(diff: str) -> str:
    """`no-diff`, `registers-only` or `structural` -- a deterministic reading of the oracle's diff."""
    lines = [line for line in (diff or "").splitlines() if line.strip()]
    minus = [mask_registers(line[1:].strip()) for line in lines
             if line.startswith("-") and not line.startswith("---")]
    plus = [mask_registers(line[1:].strip()) for line in lines
            if line.startswith("+") and not line.startswith("+++")]
    if not minus and not plus:
        return "no-diff"
    return "registers-only" if sorted(minus) == sorted(plus) else "structural"


def size_bucket(size: int | None) -> str:
    size = size or 0
    for limit, name in ((32, "tiny"), (96, "small"), (256, "medium")):
        if size <= limit:
            return name
    return "large"


def eligible_functions(conn: sqlite3.Connection, *, held_out: set[str]) -> dict[str, dict]:
    rows = conn.execute(
        "select f.name, f.size, sum(case when coalesce(a.exact,0)=1 then 1 else 0 end) as exact_n "
        "from functions f join attempts a on a.func_addr = f.addr "
        "group by f.addr, f.name, f.size").fetchall()
    return {name: {"size": size, "exact_attempts": exact_n}
            for name, size, exact_n in rows if name not in held_out}


def build_clusters(conn: sqlite3.Connection, *, held_out: set[str]) -> dict:
    """One member per FUNCTION, counting all of its attempts.

    THE FIRST VERSION COUNTED ATTEMPTS BY COUNTING MEMBERS, and each member was produced by re-querying
    the function's single best attempt -- so a function with four stored attempts contributed four
    IDENTICAL members. The cluster then reported `attempts: 4` for two distinct functions and its
    examples listed one function three times, which inflates exactly the frequency signal the scheduler
    sorts on. Fixed by grouping per function once, taking `min(a.id)` so the bare columns come from a
    deterministic representative attempt (SQLite pairs bare columns with the min/max row), and summing
    the real attempt count.
    """
    rows = conn.execute(
        "select f.name, f.size, count(*) as attempts, min(a.id) as first_id, "
        "       a.compiled, a.compiler_stderr, a.diff_summary, a.strategy "
        "from attempts a join functions f on f.addr = a.func_addr "
        "group by a.func_addr").fetchall()
    per_cluster: dict[tuple, list] = defaultdict(list)
    eligible = 0
    for name, size, attempts, _first_id, compiled, stderr, diff, strategy in rows:
        if name in held_out:
            continue
        eligible += 1
        key = (normalize_error(stderr) if not compiled else "compiled",
               residual_kind(diff), size_bucket(size))
        per_cluster[key].append({"function": name, "compiled": bool(compiled),
                                 "strategy": strategy, "size": size, "attempts": attempts})

    clusters = []
    for key, members in per_cluster.items():
        error_class, residual, size = key
        members.sort(key=lambda m: (-m["attempts"], m["function"]))
        clusters.append({
            # A STABLE digest of the feature triple. `hash()` is salted per process, so the same
            # cluster was getting a different id on every run while the queue is written to disk and
            # cited by id -- unreproducible provenance for a file the researcher reads.
            "cluster_id": hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:12],
            "error_class": error_class, "residual_kind": residual, "size_bucket": size,
            "attempts": sum(m["attempts"] for m in members),
            # A LIST of names, not a count: the coordinator uses it to keep the transfer panel away
            # from the functions that motivated the finding, so a count here would silently disable
            # the exclusion and the panel would be scored on the very functions the hypothesis came
            # from.
            "functions": [m["function"] for m in members],
            "examples": members[:5],
        })
    # THE PRIORITY HEURISTIC, DECLARED RATHER THAN IMPLIED (spec §4: log the heuristic, do not present
    # it as learned value). Breadth first, then raw attempts. Attempt count alone is dominated by a
    # handful of functions the campaign retried thousands of times -- the largest cluster by attempts
    # was ONE function with 2,160 -- so sorting on it would schedule research into the retry policy
    # rather than into how widespread a failure is. Distinct functions is a proxy for "how many tasks
    # this unblocks", which is what a bounded round should spend its calls on.
    clusters.sort(key=lambda c: (-len(c["functions"]), -c["attempts"], c["error_class"],
                                 c["residual_kind"]))
    return {"schema_version": SCHEMA_VERSION, "clusters": clusters,
            "eligible_functions": eligible, "held_out_excluded": len(held_out)}


def assert_no_held_out(payload: dict, held_out: set[str]) -> None:
    """The written queue is re-checked, not trusted: a filter bug must not become a leak.

    WHOLE NAMES, not substrings. The first version searched the serialised JSON for each held-out name
    and immediately failed on `guMtxIdentF`, which merely CONTAINS the held-out `guMtxIdent` -- a false
    alarm that would have been "fixed" by dropping the check. Function names here share prefixes
    constantly (`__osPopThread`, `__osPopThreadMain`), so the guard uses word boundaries and also
    checks the structured fields exactly. A guard that cries wolf on its first run is a guard that gets
    deleted, which is the more expensive failure.
    """
    exact = set()
    for cluster in payload.get("clusters", []):
        exact.update(cluster.get("functions") or [])
        exact.update(entry.get("function") for entry in (cluster.get("examples") or []))
    leaked_exact = sorted(exact & held_out)
    text = json.dumps(payload)
    leaked_text = sorted(name for name in held_out
                         if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text))
    if leaked_exact or leaked_text:
        raise AssertionError(
            f"held-out function names reached the demand queue: exact={leaked_exact[:5]} "
            f"text={leaked_text[:5]}")


def held_out_names(splits_path: Path, extra_result_files: list[Path]) -> set[str]:
    """Every function the experiment must not show the researcher: the test split and past panels."""
    names: set[str] = set()
    if splits_path.exists():
        payload = json.loads(splits_path.read_text("utf-8"))
        names.update(payload.get("test") or [])
    for path in extra_result_files:
        if not path.exists():
            continue
        payload = json.loads(path.read_text("utf-8"))
        for row in payload.get("rows", []):
            if row.get("function"):
                names.add(row["function"])
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", type=Path, default=Path.home() / "decomp/kb-sbk1.sqlite")
    ap.add_argument("--splits", type=Path,
                    default=ROOT / "eval/results/tool-action-20260921/splits.json")
    ap.add_argument("--panel-result", type=Path, action="append", default=[],
                    help="result files whose functions are already explored and must stay out")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/narrow-rsi-20260921/demand-queue.json")
    args = ap.parse_args(argv)

    held_out = held_out_names(args.splits, args.panel_result or [
        ROOT / "eval/results/tool-agent-20260920/head-to-head.json",
        ROOT / "eval/results/tool-action-20260921/eval-adapter-panel-dev.json"])
    conn = sqlite3.connect(f"file:{args.kb}?mode=ro", uri=True)
    payload = build_clusters(conn, held_out=held_out)
    conn.close()
    assert_no_held_out(payload, held_out)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(args.out), "clusters": len(payload["clusters"]),
                      "eligible_functions": payload["eligible_functions"],
                      "held_out_excluded": payload["held_out_excluded"],
                      "top": [{"error_class": c["error_class"][:40], "residual": c["residual_kind"],
                               "size": c["size_bucket"], "attempts": c["attempts"],
                               "functions": len(c["functions"])}
                              for c in payload["clusters"][:8]]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
