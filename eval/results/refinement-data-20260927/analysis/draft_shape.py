"""Can a deterministic check tell a good draft from a wrong-shaped one? Read-only.

For each function's best compiled ROOT draft, classify its stored instruction diff with
solver.signals.analyse (the same classifier the residual packet uses) and relate the draft's
residual KIND to whether the function ever went exact, holding the draft score band fixed.

Prediction if the basin theory is right: at equal score, drafts whose residual is SHAPE-preserving
(same instruction count, no structural faults) convert far more often than drafts with structural
faults. If the kind adds nothing beyond the score, the theory is wrong.
"""
import collections
import sqlite3
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import signals  # noqa: E402

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
children = {r[0] for r in db.execute("select distinct child_attempt_id from attempt_edges")}
exact = {r[0] for r in db.execute("select distinct func_addr from attempts where exact=1")}
size = dict(db.execute("select addr, insn_count from functions"))
best = {}
for aid, addr, score in db.execute(
        "select id, func_addr, score from attempts where coalesce(compiled,0)=1 "
        "and coalesce(exact,0)=0 and score is not null"):
    if aid not in children and (addr not in best or score > best[addr][0]):
        best[addr] = (score, aid)


def kind(sig) -> str:
    if sig.instr_delta != 0 or sig.structural:
        return "shape-wrong"          # different length or structural faults
    if sig.regalloc or sig.ordering:
        return "regs/order-only" if not (sig.layout or sig.offset or sig.width or sig.immediate
                                         or sig.reloc) else "shape-ok,mixed"
    return "shape-ok,operands"        # layout/offset/width/immediate/reloc only


cells = collections.defaultdict(lambda: [0, 0])
per_kind = collections.defaultdict(lambda: [0, 0])
for addr, (score, aid) in best.items():
    diff = db.execute("select diff_summary from attempts where id=?", (aid,)).fetchone()[0] or ""
    sig = signals.analyse(diff, score, False, True)
    k = kind(sig)
    band = ">=95" if score >= 95 else "85-95" if score >= 85 else "70-85" if score >= 70 else "<70"
    n = size.get(addr) or 0
    sb = "<=80" if n <= 80 else ">80"
    cells[(band, k)][0] += 1
    cells[(band, k)][1] += addr in exact
    per_kind[(sb, k)][0] += 1
    per_kind[(sb, k)][1] += addr in exact
print("draft score band x residual kind -> functions, share that went exact")
for band in (">=95", "85-95", "70-85", "<70"):
    for k in ("regs/order-only", "shape-ok,operands", "shape-ok,mixed", "shape-wrong"):
        n, e = cells[(band, k)]
        if n:
            print(f"  {band:6s} {k:18s} {n:4d}  exact {e/n:6.1%}")
print("\nsize x residual kind")
for sb in ("<=80", ">80"):
    for k in ("regs/order-only", "shape-ok,operands", "shape-ok,mixed", "shape-wrong"):
        n, e = per_kind[(sb, k)]
        if n:
            print(f"  {sb:5s} {k:18s} {n:4d}  exact {e/n:6.1%}")
