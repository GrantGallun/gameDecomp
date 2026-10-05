"""Forward discovery: the derived evidence-at-site mechanism on every function the population arms left unsolved.

Base: derive.py's pre-registered rules. Extension (added after the retrodiction, before this run): the ISA->C
spelling of casts, so surplus sign/zero-extension can be undone at the source -- `sll`/`sra` by 16 or 24 is a
`(s16)`/`(s8)` cast, `andi 0xffff`/`0xff` is `(u16)`/`(u8)` or `& 0xFFFF`/`& 0xFF`. Applied at the best compiled
node of each unsolved function's routed search. Per signature class it tallies instances, located instances,
candidates produced, and why the rest declined, so each class is either a derived mechanism (after compiling)
or a named missing model.
"""
import collections
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from derive import derive, replace_on_line, to_int, last_operand  # noqa: E402
from signatures import signatures  # noqa: E402

UNSOLVED = json.loads((HERE.parent / "measured-potential-20260922/potential.json").read_text())["ranked"]
ROWS = Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"
SOLVED_SINCE = {"dispatchRacePlayerMode07CourseObject"}
CAP = 16
CAST = {("sll", 16): "s16", ("sra", 16): "s16", ("sll", 24): "s8", ("sra", 24): "s8",
        ("andi", 0xFFFF): "u16", ("andi", 0xFF): "u8"}


def cast_removal(source, sig, line, cand):
    op = sig.split(":", 1)[1]
    imm = to_int(last_operand(cand) or "")
    ctype = CAST.get((op, imm))
    if line is None or ctype is None:
        return []
    s = replace_on_line(source, line, rf"\(\s*{ctype}\s*\)\s*", "")
    return [s] if s else []


def main():
    tally = collections.defaultdict(collections.Counter)
    probes = []
    for name in UNSOLVED:
        if name in SOLVED_SINCE:
            continue
        row = json.loads((ROWS / f"{name}--routed.json").read_text())
        if not row.get("world"):
            continue
        world = json.loads(Path(row["world"]).read_text())["world"]
        compiled = [n for n in world["nodes"] if n["verdict"]["compiled"] and not n["verdict"]["exact"]]
        if not compiled:
            continue
        best = max(compiled, key=lambda n: n["verdict"]["score"])
        v = best["verdict"]
        seen, cands = {best["source"]}, []
        for sig, expressible, line, target, cand in signatures(v.get("diff") or "", v.get("source_attribution")):
            t = tally[sig]
            t["instances"] += 1
            if not expressible:
                t["not_source_expressible"] += 1
                continue
            if line is None:
                t["not_located"] += 1
                continue
            made = derive(best["source"], sig, line, target, cand)
            if sig.startswith("extra:"):
                made += cast_removal(best["source"], sig, line, cand)
            made = [m for m in made if m not in seen]
            if not made:
                t["no_rule_or_no_token"] += 1
                continue
            t["produced"] += 1
            for m in made:
                if len(cands) < CAP:
                    seen.add(m)
                    cands.append((sig, m))
        for i, (sig, m) in enumerate(cands):
            probes.append({"function": name, "label": f"forward:{sig}:{i}", "source": m,
                           "parent_score": v["score"]})
    (HERE / "probes-forward.json").write_text(json.dumps(probes, indent=1))
    rows = sorted(tally.items(), key=lambda kv: -kv[1]["produced"])
    (HERE / "forward-tally.json").write_text(json.dumps({k: dict(v) for k, v in rows}, indent=1))
    print(f"{'signature':22} {'inst':>5} {'located':>7} {'produced':>8} {'no rule/token':>13} {'not expr':>8}")
    for sig, t in rows[:24]:
        print(f"{sig:22} {t['instances']:5} {t['instances'] - t['not_located'] - t['not_source_expressible']:7} "
              f"{t['produced']:8} {t['no_rule_or_no_token']:13} {t['not_source_expressible']:8}")
    print("candidates:", len(probes), "functions:", len({p['function'] for p in probes}))


if __name__ == "__main__":
    main()
