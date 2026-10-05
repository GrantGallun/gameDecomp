"""Summarize stack-probe/*.json written so far: python3 stack_progress.py"""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = [json.loads(p.read_text()) for p in sorted((HERE / "stack-probe").glob("*.json")) if p.name != "summary.json"]
print(dict(Counter(r["outcome"] for r in rows)))
for r in rows:
    print(f"  {r['outcome']:9} {r.get('baseline_score')} -> {r.get('best_score')} {r['function'][:40]:40} {r.get('path')} {r.get('error', '')[:120]}"
          f" left={r.get('remaining_deltas')}")
