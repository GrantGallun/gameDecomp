"""Section sizes of real model prompts for non-compiling parents (read-only campaign.sqlite).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/prompt_sections.py [--dump FUNCTION]

Splits each prompt at its uppercase section headers and reports byte sizes; aggregates over the latest
generation-error and valid proposals whose parent did not compile. --dump writes one full prompt to disk.
"""
import json
import re
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
HEADER = re.compile(r"(?m)^(?:\n)?([A-Z][A-Z0-9 /,()\-]{6,}[A-Z)]):?")

with sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
    rows = conn.execute(
        "SELECT p.id, p.status, f.name, length(p.prompt_context), p.prompt_context FROM model_proposals p "
        "JOIN attempts a ON a.id = p.parent_attempt_id JOIN functions f ON f.addr = a.func_addr "
        "WHERE a.compiled = 0 ORDER BY p.id DESC LIMIT 600").fetchall()
sizes = defaultdict(list)
statuses = defaultdict(int)
for pid, status, name, length, prompt in rows:
    statuses[status] += 1
    marks = [(m.start(), m.group(1).strip()) for m in HEADER.finditer(prompt)]
    marks = [(0, "PREAMBLE")] + marks + [(len(prompt), "END")]
    for (start, title), (stop, _next) in zip(marks, marks[1:]):
        sizes[title].append(len(prompt[start:stop].encode()))
    sizes["TOTAL"].append(len(prompt.encode()))
print("proposal statuses (non-compiling parents):", dict(statuses))
print(f"{'section':60} {'n':>5} {'median':>8} {'p90':>8} {'max':>8}")
for title, values in sorted(sizes.items(), key=lambda kv: -statistics.median(kv[1]) * len(kv[1])):
    values.sort()
    if len(values) < 20:
        continue
    print(f"{title[:60]:60} {len(values):5} {statistics.median(values):8.0f} {values[int(len(values) * .9)]:8} {values[-1]:8}")
if "--dump" in sys.argv:
    function = sys.argv[sys.argv.index("--dump") + 1]
    prompt = next((p for _i, s, n, _l, p in rows if n == function), None)
    if prompt:
        (HERE / f"prompt-{function}.txt").write_text(prompt)
        print("dumped", len(prompt))
