"""For a stuck node, show what its model jobs saw and proposed (read-only receipts + attempts DB rows).

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/ninety-census-20260914/model_view.py FUNCTION
"""
import json
import sqlite3
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

name = sys.argv[1]
node = campaign_state.read(RUN / "campaign.json")["nodes"][name]
for job in node.get("jobs", []):
    if job.get("model") is not True:
        continue
    receipt = json.loads(Path(job["receipt"]).read_text())
    generations = receipt.get("generations") if isinstance(receipt.get("generations"), list) else []
    invalid = receipt.get("invalid_proposals") if isinstance(receipt.get("invalid_proposals"), list) else []
    receipt["invalid_proposals"] = invalid
    print(f"\n== {job['profile']} calls={receipt.get('calls_attempted')} invalid={len(invalid)} "
          f"compiling_children={receipt.get('compiling_children')} best_compiled={(receipt.get('best_residual') or {}).get('compiled')}")
    for generation in generations[:3]:
        if isinstance(generation, dict):
            print("   generation keys", sorted(generation)[:20])
            for key in ("status", "hypothesis", "edits", "reason", "compiler_error", "prompt_sha256"):
                if key in generation:
                    print("     ", key, str(generation[key])[:400])
    for invalid in (receipt.get("invalid_proposals") or [])[:3]:
        print("   invalid:", str(invalid)[:400])
    for line in (receipt.get("log") or [])[-6:]:
        print("   log:", str(line)[:300])
with sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
    rows = conn.execute("SELECT a.id, a.strategy, a.compiled, substr(a.compiler_stderr, 1, 200), length(a.prompt_context), "
                        "a.prompt_context FROM attempts a JOIN functions f ON f.addr = a.func_addr WHERE f.name = ? "
                        "AND a.prompt_context IS NOT NULL AND a.prompt_context != '' ORDER BY a.id DESC LIMIT 2", (name,)).fetchall()
for row in rows:
    context = row[5] or ""
    marker = context.find("COMPILER")
    print(f"\nattempt {row[0]} {row[1]} compiled={row[2]} stderr={row[3]!r} prompt_chars={row[4]}")
    print("   prompt excerpt around COMPILER:", context[max(0, marker - 200):marker + 900] if marker >= 0 else context[:600])
