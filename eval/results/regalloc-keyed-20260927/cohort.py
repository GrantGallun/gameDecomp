"""Freeze the keyed-resolution A/B cohort (read-only).

Pending functions in the live campaign whose residual passes the SAME gate agentrepair uses before
running register search (`regalloc_search.register_dominant`), whose recorded source still matches its
hash, and which neither ledger (campaign.sqlite, kb-sbk1) has matched. Seeded sample.

    python3 eval/results/regalloc-keyed-20260927/cohort.py [n]
"""
import collections
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_state  # noqa: E402
from solver import regalloc_search  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
RUN = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
nodes = campaign_state.read(RUN / "campaign.json")["nodes"]
matched = set()
for db in (RUN / "campaign.sqlite", Path("/home/grant/decomp/kb-sbk1.sqlite")):
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as k:
        matched |= {r[0] for r in k.execute(
            "select distinct f.name from attempts a join functions f on f.addr=a.func_addr where a.exact=1")}


def wsl_path(p):
    p = str(p)
    if p[1:3] == ":\\":
        p = "/mnt/" + p[0].lower() + "/" + p[3:]
    return Path(p.replace("\\", "/"))


why = collections.Counter()
pool = []
for name, n in sorted(nodes.items()):
    faults = (n.get("residual") or {}).get("faults") or {}
    if n.get("status") != "pending":
        why["not pending"] += 1
        continue
    if name in matched:
        why["matched in a ledger"] += 1
        continue
    if not regalloc_search.register_dominant(faults):
        why["not register-dominant"] += 1
        continue
    source = wsl_path(n.get("source", ""))
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != n.get("source_sha256"):
        why["source missing or changed"] += 1
        continue
    why["eligible"] += 1
    pool.append({"name": name, "source": str(source), "source_sha256": n["source_sha256"],
                 "attempt_id": n.get("attempt_id"), "score": n.get("score"), "faults": faults,
                 "instruction_count": n.get("instruction_count")})
random.Random(20260927).shuffle(pool)
sample = sorted(pool[:N], key=lambda r: r["name"])
(HERE / "cohort.json").write_text(json.dumps({"seed": 20260927, "pool_size": len(pool), "reasons": why,
                                              "selection": __doc__.strip().splitlines()[2:6],
                                              "functions": sample}, indent=1))
print(dict(why), "sample", len(sample))
