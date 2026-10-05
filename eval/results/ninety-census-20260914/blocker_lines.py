"""Group the non-compiling nodes by message and the construct on the offending line (read-only).

    python eval/results/ninety-census-20260914/blocker_lines.py
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = json.loads((HERE / "not_compiled.json").read_text())


def construct(row):
    text, message = row.get("line_text") or "", row["message"]
    if "M2C_" in text:
        return "m2c_placeholder"
    if re.search(r"(^|\W)\?(\W|$)", text):
        return "unknown_type_?"
    if "undefined" in message:
        name = re.search(r"'([^']*)'", message)
        name = name.group(1) if name else ""
        return ("undefined:D_/func_" if re.match(r"(D_|func_|jtbl_|B_)", name) else
                "undefined:m2c_var" if re.match(r"(var_|temp_|phi_|sp[0-9A-F]+|arg\d|saved_reg|unaligned)", name) else
                "undefined:named")
    if "->unk-" in text or re.search(r"->unk\w*-", text):
        return "cast_syntax"
    if re.match(r"for\s*\(\s*(int|s32|u32)", text):
        return "c99_for_declaration"
    return "other"


groups = defaultdict(list)
for row in rows:
    groups[construct(row)].append(row)
print(json.dumps({k: len(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}, indent=1))
for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    print(f"\n== {key} ({len(members)})")
    for row in members:
        print(f"  {row['function'][:34]:34} jobs={row['jobs']:<3} stall={int(row['stalled'])} {row['message'][:44]:44} | {(row.get('line_text') or '')[:100]}")
