"""H7: remove `register` from each mining reference that uses it and recompile. See PROTOCOL.md.

    python3 ablate.py   -> ablate.json (aggregates and opcode deltas; no reference source is written here)
"""
import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import mine  # noqa: E402
import pairs  # noqa: E402

REG = re.compile(r"\bregister\s+")


def opt(ws: Path) -> str:
    found = {m for p in ws.glob(".compiler-*.json") if p.name != ".compiler-target.json"
             for m in re.findall(r'"C_OPT": "([^"]*)"', p.read_text())}
    return ",".join(sorted(found)) or "?"


def main():
    mirror = pairs.mirror_repo()
    out = []
    for path in sorted((mine.E / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        ref = row.get("ref") or {}
        if not ref.get("compiled") or not REG.search(row.get("ref_def", "")):
            continue
        if mine.mask(ref["dump"]) != mine.mask(row["target_dump"]):
            continue
        ws = mirror / "nonmatchings" / row["function"]
        code = (ws / "ref.c").read_text()
        again = pairs.build(ws, "ref_again", code)
        stripped = pairs.build(ws, "noreg", REG.sub("", code))
        t = mine.mask(row["target_dump"])
        rec = {"function": row["function"], "opt": opt(ws),
               "ref_reproduces": again["compiled"] and mine.mask(again["dump"]) == t,
               "noreg_compiled": stripped["compiled"]}
        if stripped["compiled"]:
            s = mine.mask(stripped["dump"])
            rec["changed"] = s != t
            bal = collections.Counter(l.split()[0] for l in t)
            bal.subtract(collections.Counter(l.split()[0] for l in s))
            rec["target_minus_noreg"] = {k: v for k, v in bal.items() if v}
            rec["len"] = [len(t), len(s)]
        out.append(rec)
    o1 = [r for r in out if r["opt"] == "-O1" and r["ref_reproduces"] and r.get("noreg_compiled")]
    o2 = [r for r in out if r["opt"] == "-O2" and r["ref_reproduces"] and r.get("noreg_compiled")]
    verdict = {"P7a (-O1 all change)": f'{sum(r["changed"] for r in o1)}/{len(o1)}',
               "P7b (-O2 none change)": f'{sum(not r["changed"] for r in o2)}/{len(o2)}',
               "P7a": bool(o1) and all(r["changed"] for r in o1),
               "P7b": bool(o2) and not any(r["changed"] for r in o2)}
    (HERE / "ablate.json").write_text(json.dumps({"rows": out, "verdict": verdict}, indent=1))
    for r in out:
        print(r)
    print(json.dumps(verdict))


if __name__ == "__main__":
    main()
