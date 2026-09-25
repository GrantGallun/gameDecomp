"""The campaign's real frontier: every campaign function without an exact attempt, its best compiled attempt, size
and residual classes. Read-only over runs/resume-pipeline-20260908/campaign.sqlite; no compiles.

Residual classes come from the project's own classifier (eval.mechanism_roadmap.classes on the attempt's diff),
grouped into families. "Close" = best score >= 95.

    python3 frontier.py -> frontier.json
"""
import collections
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap  # noqa: E402

HERE = Path(__file__).resolve().parent
H = Path.home() / "decomp"
camp = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
exact = {a for (a,) in camp.execute("select distinct func_addr from attempts where exact=1")}
funcs = {a: (n, ic) for a, n, ic in camp.execute("select addr, name, insn_count from functions")}
best = {}
for a, score, diff, cid in camp.execute(
        "select func_addr, score, diff_summary, id from attempts where compiled=1 and exact=0 and score is not null"):
    if a in exact:
        continue
    if a not in best or score > best[a][0]:
        best[a] = (score, diff, cid)
attempted = {a for (a,) in camp.execute("select distinct func_addr from attempts")}
never_compiled = attempted - exact - set(best)


def size(n):
    return "small" if n is None or n < 50 else "medium" if n < 150 else "large"


def family(k: str) -> str:
    if k == "field:register":
        return "register"
    if k in ("field:branch",) or re.match(r"(extra|missing|opcode):(b|beq|bne|beqz|bnez|blez|bgtz|bltz|bgez|j|jr|jal)\b", k):
        return "branch/call"
    if k in ("field:offset",):
        return "offset"
    if k in ("field:immediate",):
        return "immediate"
    if re.match(r"(extra|missing|opcode):.*(andi|sll|sra|srl|lb|lbu|lh|lhu|sb|sh)\b", k):
        return "width/sign"
    if re.match(r"(extra|missing|opcode):.*(move|addiu|addu|lui|or)\b", k):
        return "temporaries/address"
    if re.match(r"(extra|missing|opcode):.*(lw|sw|lwc1|swc1)\b", k):
        return "load/store"
    if re.match(r"(extra|missing|opcode):.*(nop)\b", k):
        return "nop/scheduling"
    if k.startswith("field:multi") or k.startswith("field:shape"):
        return "multi-field"
    return "other:" + k.split(":")[0]


rows = []
for a, (score, diff, cid) in best.items():
    name, ic = funcs.get(a, (hex(a), None))
    fam = set()
    if diff and diff.lstrip().startswith(("---", "@@")):
        try:
            fam = {family(k) for k, *_ in mechanism_roadmap.classes(diff, None)}
        except Exception:
            fam = {"unclassifiable"}
    else:
        fam = {"no-diff-recorded"}
    rows.append({"function": name, "insns": ic, "size": size(ic), "score": score, "families": sorted(fam),
                 "attempt": cid})

by_size = collections.Counter(r["size"] for r in rows)
close = [r for r in rows if r["score"] >= 95]
bands = collections.Counter(("99+" if r["score"] >= 99 else "95-99" if r["score"] >= 95 else "90-95" if r["score"] >= 90
                             else "<90", r["size"]) for r in rows)
fam_close = collections.Counter(f for r in close for f in r["families"])
sole_close = collections.Counter(r["families"][0] for r in close if len(r["families"]) == 1)
combo_close = collections.Counter(" + ".join(r["families"]) for r in close)
fam_large = collections.Counter(f for r in rows if r["size"] == "large" for f in r["families"])
nfam = collections.defaultdict(list)
for r in rows:
    nfam[r["size"]].append(len(r["families"]))
out = {"campaign_functions": len(funcs), "exact": len(exact), "compiled_not_exact": len(rows),
       "attempted_never_compiled": len(never_compiled), "never_attempted": len(funcs) - len(attempted),
       "compiled_not_exact_by_size": dict(by_size),
       "score_bands": {f"{b}|{s}": n for (b, s), n in sorted(bands.items())},
       "close_ge95": len(close), "close_by_size": dict(collections.Counter(r["size"] for r in close)),
       "close_family_presence": fam_close.most_common(), "close_single_family": sole_close.most_common(),
       "close_combinations": combo_close.most_common(12),
       "large_family_presence": fam_large.most_common(),
       "median_families_per_function": {k: statistics.median(v) for k, v in nfam.items()},
       "rows": sorted(rows, key=lambda r: -r["score"])}
(HERE / "frontier.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1))
