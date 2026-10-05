"""Lossless compression savings on real non-compiling-parent prompts (read-only campaign.sqlite).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/compression_savings.py

Per prompt: bytes of assembly blocks before/after shifts.strip_asm, bytes of JSON blocks before/after
compact re-encoding, and how many prompts would fit the 51,488-byte budget (num_predict 6000) after.
"""
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from solver import shifts  # noqa: E402

LIMIT = (32768 - 6000 - 1024) * 2
FENCE = re.compile(r"```(?:json|c)?\n(.*?)\n```", re.S)
with sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
    rows = conn.execute("SELECT p.status, p.prompt_context FROM model_proposals p JOIN attempts a ON a.id = p.parent_attempt_id "
                        "WHERE a.compiled = 0 ORDER BY p.id DESC LIMIT 600").fetchall()
before, after, asm_saved, json_saved, fit_before, fit_after = [], [], [], [], 0, 0
for status, prompt in rows:
    compact = prompt
    saved_asm = saved_json = 0
    # Annotated assembly blocks, fenced or following the READ-ONLY TARGET INSTRUCTIONS header.
    for block in set(re.findall(r"(?ms)^glabel \w+\n.*?(?=\n```|\n\n[A-Z][A-Z ]{6,}|\Z)", prompt)):
        stripped = shifts.strip_asm(block)
        saved_asm += len(block.encode()) - len(stripped.encode())
        compact = compact.replace(block, stripped)
    # Pretty-printed JSON: re-encode any parseable top-level object compactly.
    for match in re.finditer(r"(?ms)^(\{\n.*?\n\})$", prompt):
        try:
            value = json.loads(match.group(1))
        except ValueError:
            continue
        dense = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        saved_json += len(match.group(1).encode()) - len(dense.encode())
        compact = compact.replace(match.group(1), dense)
    before.append(len(prompt.encode()))
    after.append(len(compact.encode()))
    asm_saved.append(saved_asm)
    json_saved.append(saved_json)
    fit_before += before[-1] <= LIMIT
    fit_after += after[-1] <= LIMIT
print(json.dumps({"prompts": len(rows), "budget_bytes": LIMIT,
                  "median_bytes_before": statistics.median(before), "median_bytes_after": statistics.median(after),
                  "median_asm_bytes_saved": statistics.median(asm_saved), "median_json_bytes_saved": statistics.median(json_saved),
                  "total_reduction": round(1 - sum(after) / sum(before), 3),
                  "fit_before": fit_before, "fit_after_lossless_compaction": fit_after}, indent=1))
