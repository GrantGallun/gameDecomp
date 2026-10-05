"""Did the brief reach the model? Inspect the recorded prompts of both arms."""
import sqlite3
from pathlib import Path

DB = Path.home() / "decomp/experiments/locality-population-20260923/attempts.sqlite"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cols = [r[1] for r in conn.execute("pragma table_info(model_proposals)")]
print("model_proposals columns:", cols)
text_cols = [c for c in cols if c in ("prompt", "prompt_text", "request", "messages", "prompt_context")]
rows = conn.execute(f"select id, run_id, {', '.join(text_cols)} from model_proposals order by id desc limit 64").fetchall()
marker = "COMPILER-ATTRIBUTED FAULTS"
with_brief = [r for r in rows if any(marker in (x or "") for x in r[2:])]
print("recent proposals:", len(rows), "containing the brief:", len(with_brief))
if rows:
    sample = max(rows, key=lambda r: sum(len(x or "") for x in r[2:]))
    body = next(x for x in sample[2:] if x)
    print("longest recorded prompt:", len(body), "chars; brief present:", marker in body)
    i = body.find(marker)
    print(body[max(0, i - 200):i + 600] if i >= 0 else body[:600])
if with_brief:
    body = next(x for x in with_brief[0][2:] if x and marker in x)
    i = body.find(marker)
    print("--- position of brief in prompt:", i, "of", len(body))
