"""Show the source line each non-compiling node's IDO error points at, and group common constructs (read-only).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/syntax_lines.py
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

state = campaign_state.read(RUN / "campaign.json")
constructs, rows = Counter(), []
for name, node in sorted(state["nodes"].items()):
    residual = node.get("residual") or {}
    if node["status"] != "pending" or residual.get("compiled") is not False:
        continue
    signature = residual.get("compiler_error_signature") or ""
    match = re.search(r"line (\d+): (.*)", signature)
    try:
        lines = Path(node["source"]).read_text().splitlines()
    except (OSError, KeyError, TypeError):
        continue
    if not match:
        continue
    number, message = int(match.group(1)), match.group(2)[:60]
    text = lines[number - 1].strip() if 0 < number <= len(lines) else "<out of range>"
    kind = ("M2C_ placeholder" if "M2C_" in text else
            "unknown type ?" if re.search(r"(^|\W)\?(\W|$)", text) else
            "goto/label" if re.search(r"\bgoto\b|^\w+:$", text) else
            "switch/jump table" if re.search(r"\bswitch\b|\bcase\b|jtbl", text) else
            "C99 declaration" if re.match(r"(for\s*\(\s*(int|s32|u32)|.*\bbool\b)", text) else
            "struct/union literal" if "{" in text and "=" in text else
            "cast syntax" if re.search(r"\(\s*\w+\s*\*?\s*\)\s*\(", text) else "other")
    if "Syntax Error" in message:
        constructs[kind] += 1
    rows.append({"function": name, "message": message, "line": number, "text": text[:140], "kind": kind,
                 "instructions": node.get("instruction_count")})
print(json.dumps(dict(constructs.most_common()), indent=1))
for row in rows:
    if "Syntax" in row["message"]:
        print(f"{row['function'][:36]:36} {row['kind'][:16]:16} {row['text'][:110]}")
Path(__file__).with_name("syntax-lines.json").write_text(json.dumps(rows, indent=1))
