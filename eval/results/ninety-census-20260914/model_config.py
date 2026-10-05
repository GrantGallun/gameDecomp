"""Print the campaign model config and the proposal-table schema (read-only)."""
import json
import sqlite3
import sys
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
sys.path.insert(0, str(RUN / "code"))
from eval import campaign_state  # noqa: E402

config = campaign_state.read(RUN / "campaign.json")["config"]
print({k: config.get(k) for k in ("model", "endpoint", "num_predict", "timeout", "model_calls", "think")})
with sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
    for (name, sql) in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql LIKE '%prompt%'"):
        print(name, sql[:600])
