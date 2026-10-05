"""Read-only stratified check: reconstruct normalized candidates from target + logged diff.

No C sources, reference answers, compiles or campaign mutations. Historical-target
compatibility is checked at every context/deletion line; retained candidate dumps
provide an independent comparison where available. This is listing evidence only.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import random
import re
import sqlite3
from pathlib import Path

from solver import invariants as inv

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def reconstruct(target: list[str], diff: str) -> list[str]:
    lines = diff.splitlines()
    if not lines:
        raise ValueError("empty diff has no independent completeness receipt")
    cursor, out, index, seen_hunk = 0, [], 0, False
    while index < len(lines):
        line = lines[index]
        if line.startswith(("--- ", "+++ ")):
            index += 1
            continue
        match = HUNK.match(line)
        if not match:
            raise ValueError("unexpected/truncated diff content")
        seen_hunk = True
        old_start, old_n, new_start, new_n = (int(match[1]), int(match[2] or 1),
                                              int(match[3]), int(match[4] or 1))
        old_pos = old_start if old_n == 0 else old_start - 1
        new_pos = new_start if new_n == 0 else new_start - 1
        if not cursor <= old_pos <= len(target):
            raise ValueError("old hunk range invalid")
        out.extend(target[cursor:old_pos])
        if len(out) != new_pos:
            raise ValueError("new hunk range invalid")
        cursor, used_old, used_new = old_pos, 0, 0
        index += 1
        while index < len(lines) and not lines[index].startswith("@@"):
            part = lines[index]
            if part.startswith("\\ No newline"):
                index += 1
                continue
            if not part or part[0] not in " +-":
                raise ValueError("invalid hunk body")
            prefix, content = part[0], part[1:]
            if prefix in " -":
                if cursor >= len(target) or target[cursor] != content:
                    raise ValueError("target context/deletion mismatch")
                cursor += 1
                used_old += 1
            if prefix in " +":
                out.append(content)
                used_new += 1
            index += 1
        if (used_old, used_new) != (old_n, new_n):
            raise ValueError("hunk count mismatch/truncation")
    if not seen_hunk:
        raise ValueError("no hunks")
    return out + target[cursor:]


def self_check():
    middle = [f"ori t0,t0,{i}" for i in range(20)]
    target = ["jal foo", "nop"] + middle + ["jr ra", "nop"]
    candidates = [[], target + ["nop"], ["nop"] + target, target[1:],
                  ["nop"] + middle + ["jal foo", "jr ra", "nop"]]
    for candidate in candidates:
        for n in (0, 1, 3):
            diff = "\n".join(difflib.unified_diff(target, candidate, "target", "candidate", n=n, lineterm=""))
            assert reconstruct(target, diff) == candidate
    moved = candidates[-1]
    diff = "\n".join(difflib.unified_diff(target, moved, "target", "candidate", n=3, lineterm=""))
    assert inv.distance(inv.parse("\n".join(target)), inv.parse("\n".join(moved))) == (0, 0, 0, 2, 0, 0)
    assert inv.distance_from_diff(diff) == (2, 0, 0, 2, 0, 0)
    try:
        reconstruct(target, diff.rsplit("\n", 1)[0])
    except ValueError:
        pass
    else:
        raise AssertionError("truncated hunk accepted")


def sign(parent, child):
    return "up" if child < parent else "down" if child > parent else "same"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--workspace", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per-stratum", type=int, default=40)
    args = ap.parse_args()
    self_check()
    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    db.execute("begin")
    snapshot = db.execute("select count(*), max(id), max(created_at) from attempts").fetchone()
    population = collections.defaultdict(list)
    for eid, p, c, name, size, ps, cs, exact, strategy in db.execute("""
        select e.rowid,p.id,c.id,f.name,f.insn_count,p.score,c.score,c.exact,c.strategy
        from attempt_edges e join attempts p on p.id=e.parent_attempt_id
        join attempts c on c.id=e.child_attempt_id join functions f on f.addr=c.func_addr
        where p.func_addr=c.func_addr and p.compiled=1 and c.compiled=1
          and coalesce(p.exact,0)=0 and p.score is not null and c.score is not null
    """):
        band = "<=30" if (size or 0) <= 30 else "31-80" if size <= 80 else "81-200" if size <= 200 else ">200"
        score = "up" if cs > ps else "down" if cs < ps else "same"
        population[(band, score)].append((eid, p, c, name, exact, strategy))
    rng = random.Random(20260928)
    sample = [(stratum, row) for stratum, rows in sorted(population.items())
              for row in rng.sample(rows, min(args.per_stratum, len(rows)))]
    target_cache, attempt_cache = {}, {}

    def measure(aid, name):
        if aid in attempt_cache:
            return attempt_cache[aid]
        diff, = db.execute("select diff_summary from attempts where id=?", (aid,)).fetchone()
        result = {"attempt": aid, "old": inv.distance_from_diff(diff or "")}
        ws = args.workspace / name
        try:
            if name not in target_cache:
                target_cache[name] = (ws / "target_object_dump_normalized.s").read_text().splitlines()
            target = target_cache[name]
            candidate = reconstruct(target, diff or "")
            result["full"] = inv.distance(inv.parse("\n".join(target)), inv.parse("\n".join(candidate)))
            result["hunks"] = sum(bool(HUNK.match(line)) for line in diff.splitlines())
            result["target_sha256"] = hashlib.sha256("\n".join(target).encode()).hexdigest()
            result["candidate_sha256"] = hashlib.sha256("\n".join(candidate).encode()).hexdigest()
            header = next((line[4:].split("\t")[0] for line in diff.splitlines() if line.startswith("+++ ")), None)
            if header:
                # Use basename only: never follow a path supplied by the diff.
                retained = ws / Path(header).name
                if retained.is_file():
                    result["retained_dump_equal"] = retained.read_text().splitlines() == candidate
            result["status"] = "reconstructed"
        except (OSError, ValueError) as exc:
            result["status"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        attempt_cache[aid] = result
        return result

    rows, strata = [], {}
    for stratum, pool in population.items():
        strata["|".join(stratum)] = {"population_edges": len(pool), "sampled": 0,
                                    "reconstructed_pairs": 0, "edge_direction_changes": 0}
    for stratum, (eid, p, c, name, exact, strategy) in sample:
        a, b = measure(p, name), measure(c, name)
        row = {"edge": eid, "parent": p, "child": c, "function": name,
               "stratum": stratum, "strategy": strategy, "child_exact": bool(exact)}
        group = strata["|".join(stratum)]
        group["sampled"] += 1
        if "full" in a and ("full" in b or exact):
            old_c = (0,) * 6 if exact else b["old"]
            new_c = (0,) * 6 if exact else b["full"]
            row.update(old_direction=sign(a["old"], old_c), full_direction=sign(a["full"], new_c))
            group["reconstructed_pairs"] += 1
            group["edge_direction_changes"] += row["old_direction"] != row["full_direction"]
        rows.append(row)
    attempts = list(attempt_cache.values())
    reconstructed = [a for a in attempts if "full" in a]
    checked = [a for a in attempts if "retained_dump_equal" in a]
    comparisons = [r for r in rows if "full_direction" in r]
    summary = {
        "snapshot_attempts_count_maxid_maxcreated": snapshot,
        "population_edges": sum(len(v) for v in population.values()),
        "sampled_edges": len(rows), "sampled_functions": len({r["function"] for r in rows}),
        "unique_attempts": len(attempts), "reconstructed_attempts": len(reconstructed),
        "vector_disagreements": sum(a["old"] != a["full"] for a in reconstructed),
        "first_level_disagreements": sum(inv.level(a["old"]) != inv.level(a["full"]) for a in reconstructed),
        "per_component_disagreements": {level: sum(a["old"][i] != a["full"][i] for a in reconstructed)
                                         for i, level in enumerate(inv.LEVELS)},
        "comparable_edges": len(comparisons),
        "edge_direction_changes": sum(r["old_direction"] != r["full_direction"] for r in comparisons),
        "retained_dump_checks": len(checked),
        "retained_dump_disagreements": sum(not a["retained_dump_equal"] for a in checked),
        "unavailable": dict(collections.Counter(a["status"] for a in attempts if "full" not in a)),
        "strata": dict(sorted(strata.items())),
        "limits": "Stratified descriptive sample, not an unweighted campaign prevalence estimate. "
                  "Reconstruction validates visible old-side lines, not hidden historical target identity. "
                  "Empty/malformed/missing artifacts remain unavailable. No compiles or reference C reads.",
    }
    db.rollback()
    db.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "attempts": attempts, "edges": rows}, indent=2))
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
