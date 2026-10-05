"""Which 90+ functions differ only in stack layout (sp-relative offsets / frame size), ignoring register-only pairs.

    python3 eval/results/ninety-census-20260914/stack_only.py

A block is 'stack' when target and candidate have the same mnemonic sequence and every differing
instruction differs only in an sp-relative immediate. Also reports 'stack+X' mixes.
"""
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRANCH = re.compile(r"^(b\w*|j|jal)$")


def norm(text):
    parts = text.split(None, 1)
    ops = parts[1].split(",") if len(parts) > 1 else []
    if BRANCH.match(parts[0]) and parts[0] not in ("jr", "jalr") and ops:
        ops = ops[:-1] + ["<t>"]
    return parts[0], ops


def stack_pair(want, got):
    wm, wo = norm(want)
    gm, go = norm(got)
    if wm != gm or len(wo) != len(go):
        return False
    for a, b in zip(wo, go):
        if a == b:
            continue
        sp = re.fullmatch(r"-?(0x[0-9a-f]+|\d+)\(sp\)", a) and re.fullmatch(r"-?(0x[0-9a-f]+|\d+)\(sp\)", b)
        imm = (wo[:2] in (["sp", "sp"],) or (len(wo) == 3 and wo[1] == "sp")) and re.fullmatch(r"-?(0x[0-9a-f]+|\d+)", a)
        if not (sp or imm):
            return False
    return True


rows, kinds = [], Counter()
for path in sorted((HERE / "diffs").glob("*.json")):
    entry = json.loads(path.read_text())
    stack = other = 0
    for d in (entry.get("compare") or {}).get("differences", []):
        if "kind" not in d:
            continue
        want, got = d["target"], d["candidate"]
        if [norm(t) for t in want] == [norm(t) for t in got]:
            continue
        if len(want) == len(got) and all(w == g or stack_pair(w, g) for w, g in zip(want, got)):
            stack += 1
        else:
            other += 1
    kind = "stack_only" if stack and not other else "stack+other" if stack else "no_stack"
    kinds[kind] += 1
    rows.append({"function": entry["function"], "score": entry["score"], "instructions": entry.get("instructions"),
                 "stack_blocks": stack, "other_blocks": other, "kind": kind,
                 "register": bool((entry.get("compare") or {}).get("register_instructions"))})
print(dict(kinds))
for r in sorted(rows, key=lambda r: (r["kind"], r["other_blocks"], -r["score"])):
    if r["kind"] == "stack_only" or (r["kind"] == "stack+other" and r["other_blocks"] <= 1):
        print(f"  {r['kind']:12} other={r['other_blocks']} stack={r['stack_blocks']:2} reg={int(r['register'])} {r['score']:7} n={r['instructions']:4} {r['function']}")
(HERE / "stack_only.json").write_text(json.dumps(rows, indent=1))
