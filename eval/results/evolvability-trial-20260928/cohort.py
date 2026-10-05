"""Freeze the evolvability/mutation-selection trial cohort into research-suite task configs (read-only).

Pending functions in the live campaign that pass the campaign's own register-search gate
(`regalloc_search.register_dominant`), unmatched in both ledgers, with the SMALLEST residuals: the
2026-09-27 keyed A/B drew from the whole pool and neither arm matched anything at budget 200, which
cannot separate policies. Ties broken by seed. Split round-robin into shards (one frozen bundle each)
so the sequential `run` command can use several cores.

Source = the node's current campaign source (hash-checked). Target = the function's isolated
workspace `target.o`; compile target from its `.compiler-target.json`. Assistance: `header_assisted`
when the source includes a reconstructed `game/` header, else `unknown` (drafts may be reference-seeded,
see memory draft-contamination), never `declared_unassisted`.

    python3 eval/results/evolvability-trial-20260928/cohort.py [n] [shards]
"""
import hashlib
import json
import random
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_state  # noqa: E402
from solver import regalloc_search  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 18
SHARDS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
SKIP = int(sys.argv[3]) if len(sys.argv) > 3 else 0      # replication: skip functions an earlier trial used
TAG = sys.argv[4] if len(sys.argv) > 4 else ""
ARMS = sys.argv[5].split(",") if len(sys.argv) > 5 else None
SEEDS = [int(x) for x in sys.argv[6].split(",")] if len(sys.argv) > 6 else None
RUN = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
WORK = Path("/home/grant/decomp/sbk1/nonmatchings")
HERE = Path(__file__).resolve().parent
SETTINGS = {
    "arms": ["production", "production_diverse", "mutation_count", "mutation_count_diverse",
             "evolvability", "evolvability_diverse"],
    "baseline": "production", "seeds": [0, 1], "budget": 128,
    "search": {"beam": 3, "depth": 4, "mutation_preview": 64, "mutation_probes": 2, "explore_rate": 0.2},
}

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


# Functions used by earlier cohorts are excluded by NAME (the pool changes as the campaign matches things,
# so rank-based skipping would not keep cohorts disjoint).
used = set()
if TAG:
    for prior in HERE.glob("cohort*.json"):
        if prior.name != f"cohort{TAG}.json":
            used |= {r["name"] for r in json.loads(prior.read_text())["functions"]}
pool = []
for name, n in sorted(nodes.items()):
    if name in used:
        continue
    faults = (n.get("residual") or {}).get("faults") or {}
    if n.get("status") != "pending" or name in matched or not regalloc_search.register_dominant(faults):
        continue
    source, ws = wsl_path(n.get("source", "")), WORK / name
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != n.get("source_sha256"):
        continue
    if not (ws / "target.o").is_file() or not (ws / ".compiler-target.json").is_file():
        continue
    target = json.loads((ws / ".compiler-target.json").read_text())
    if target.get("function") != name or not str(target.get("target", "")).startswith("build/"):
        continue
    text = source.read_text(errors="replace")
    pool.append({"name": name, "total": sum(faults.values()), "source": str(source),
                 "target_object": str(ws / "target.o"), "compile_target": target["target"],
                 "assistance": "header_assisted" if '#include "game/' in text else "unknown",
                 "cluster": Path(target["target"]).stem, "attempt_id": n.get("attempt_id"),
                 "faults": faults})
rng = random.Random(20260928)
rng.shuffle(pool)
pool.sort(key=lambda r: r["total"])                     # stable: seeded order within equal residuals
chosen = pool[SKIP:SKIP + N]
if ARMS:
    SETTINGS["arms"] = ARMS
if SEEDS:
    SETTINGS["seeds"] = SEEDS
for shard in range(SHARDS):
    tasks = [{"id": f"t{i:03d}", "function": r["name"], "source": r["source"],
              "target_object": r["target_object"], "compile_target": r["compile_target"],
              "assistance": r["assistance"], "cluster": r["cluster"],
              "provenance": {"attempt_id": r["attempt_id"], "faults": r["faults"],
                             "target_origin": "campaign workspace target.o"}, "proposals": []}
             for i, r in enumerate(chosen) if i % SHARDS == shard]
    (HERE / f"tasks{TAG}-{shard}.json").write_text(json.dumps({"tasks": tasks, "settings": SETTINGS}, indent=1))
(HERE / f"cohort{TAG}.json").write_text(json.dumps({"seed": 20260928, "pool_size": len(pool), "n": len(chosen),
                                              "functions": chosen}, indent=1))
print(f"pool {len(pool)}, chosen {len(chosen)}, residual totals {[r['total'] for r in chosen]}, "
      f"assistance {sorted({r['assistance'] for r in chosen})}")
