"""Exploration: mining pairs whose reference adds `register` -- what it qualifies, and the residual lines."""
import json, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import mine
for path in sorted((mine.E / "rows").glob("*.json")):
    row = json.loads(path.read_text())
    ref, draft = row.get("ref") or {}, row.get("draft") or {}
    if not (ref.get("compiled") and draft.get("compiled") and row.get("target_dump")):
        continue
    if mine.mask(ref["dump"]) != mine.mask(row["target_dump"]):
        continue
    a, b = mine.shape(row["draft_def"]), mine.shape(row["ref_def"])
    if b["register"] <= a["register"]:
        continue
    res = mine.residual(row["target_dump"], draft["dump"])
    regs = [l.strip() for l in row["ref_def"].splitlines() if re.search(r"\bregister\b", l)]
    recipe = open(next((mine.E / "repo/nonmatchings" / row["function"]).glob(".compiler-*.json"))).read() \
        if list((mine.E / "repo/nonmatchings" / row["function"]).glob(".compiler-*.json")) else ""
    opt = re.search(r'"C_OPT": "([^"]*)"', recipe)
    print(f'{row["function"]:40s} {opt.group(1) if opt else "?":4s} draft={draft["score"]} exact={res is None} '
          f'sig={sorted(f for f in (res[0] if res else []) if f in ("R:opcode:move/sw","R:opcode:addiu/lw","R:opcode:nop/sw"))}')
    for r in regs:
        print("      ", r)
    if len(sys.argv) > 1 and row["function"] == sys.argv[1]:
        print(row["draft_def"]); print("=" * 50); print(row["ref_def"]); print(draft["diff"][:3000])
