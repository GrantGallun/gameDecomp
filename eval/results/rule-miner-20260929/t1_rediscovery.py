"""T1: on osMotorStart's greedy-chain stages, does the mined table rank the move that worked next in the top 3?

Stage after `unaligned_copy`: the next working move was counted_loop (A rule loop:rotated->for).
Stage after `counted_loop`: the next working move was temp_copyback (A rule temp:copyback->direct).
Only Engine A generic rewrites are ranked (specialised generators are not in the table).
"""
import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import rewrite_library, rule_miner

db = sqlite3.connect("file:/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite?mode=ro", uri=True)
table = rule_miner.Table.load()
for fn in ("osMotorStart", "osMotorStop"):
    addr = db.execute("select addr from functions where name=?", (fn,)).fetchone()[0]
    rows = db.execute("select strategy, source_code, diff_summary, score from attempts where func_addr=? and strategy like "
                      "'greedy-chain:%' and compiled=1 order by id", (addr,)).fetchall()
    stages = {}
    for st, src, d, sc in rows:
        step = st.split(":")[1]
        if step not in stages or sc > stages[step][2]:
            stages[step] = (src, d, sc)                  # the step's best-scoring attempt is the state the chain kept
    for stage, want in (("unaligned_copy", "loop:rotated->for"), ("counted_loop", "temp:copyback->direct")):
        if stage not in stages:
            print(fn, stage, "stage missing"); continue
        src, diff, sc = stages[stage]
        residual = rule_miner.features(diff)
        ranked = []
        for rule, label, _new in rewrite_library.all_variants(src, fn):
            ranked.append((table.score("A:" + rule_miner.inverse(rule), residual), rule))
        best = {}
        for s, r in ranked:
            best[r] = max(best.get(r, 0.0), s)
        order = sorted(best.items(), key=lambda kv: -kv[1])
        rank = next((i + 1 for i, (r, s) in enumerate(order) if r == want), None)
        print(f"{fn:14s} stage after {stage:15s} score {sc}  want {want:24s} rank {rank} of {len(order)} "
              f"{'PASS' if rank and rank <= 3 and best[want] > 0 else 'FAIL'}  top: {[(r, round(s, 2)) for r, s in order[:4]]}")
