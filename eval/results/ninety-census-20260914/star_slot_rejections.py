"""How many campaign model proposals named a slot on a `*`-leading code line (never a slot before 2026-09-15)?

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/star_slot_rejections.py
"""
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
counts = Counter()
with sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
    for status, edits, source, compiled in conn.execute(
            "SELECT p.status, p.edits, a.source_code, a.compiled FROM model_proposals p "
            "JOIN attempts a ON a.id = p.parent_attempt_id WHERE p.edits LIKE '%slot%'"):
        counts["proposals_with_slots"] += 1
        try:
            items = json.loads(edits)
        except ValueError:
            continue
        lines = source.splitlines()
        hit = False
        for edit in items:
            found = re.search(r"L(\d+)$", edit.get("slot") or "")
            if found and 0 < int(found.group(1)) <= len(lines):
                text = lines[int(found.group(1)) - 1].lstrip()
                if text.startswith("*") and not text.startswith(("*/", "* ")) or re.match(r"^\*\s*\(", text):
                    hit = True
        if hit:
            counts[f"star_line_slot:{status}"] += 1
            counts[f"star_line_slot:{status}:{'compiled_parent' if compiled else 'noncompiling_parent'}"] += 1
        counts[f"all:{status}"] += 1
print(json.dumps(dict(sorted(counts.items())), indent=1))
