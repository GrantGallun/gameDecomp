"""How much does scalar coalescing reach across the whole campaign? One-step sweep, no reference.

`solver/scalar_coalesce.variants` (Astra, eval/results/coalescing-factorial-20260928) matched its two motivating
functions. That shows it fires, not what it yields. This applies it once to the CURRENT campaign source of
every pending function unmatched in both ledgers, compiles up to 8 variants each, and records exact matches
(workspace.repair_complete: byte certificate + frontend) and score changes. A lower bound: one coalescing step,
no composition with other families. Matching sources are saved for a proper import; nothing is written to a
ledger here.

    python3 sweep.py      (WSL)
"""
import collections
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_state, campaign_workers  # noqa: E402
from solver import scalar_coalesce, workspace  # noqa: E402

RUN = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
NATIVE = Path("/home/grant/decomp/experiments/coalescing-sweep-20260928")
HERE = Path(__file__).resolve().parent
LIMIT = 8
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


tally = collections.Counter()
matches = []
out = HERE / "sweep.jsonl"
out.unlink(missing_ok=True)
for name, n in sorted(nodes.items()):
    if n.get("status") != "pending" or name in matched:
        continue
    source_path = wsl_path(n.get("source", ""))
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != n.get("source_sha256"):
        tally["source missing or changed"] += 1
        continue
    tally["pending functions"] += 1
    source = source_path.read_text(errors="replace")
    try:
        variants = list(scalar_coalesce.variants(source, name))[:LIMIT]
    except Exception as exc:
        tally["generator error"] += 1
        continue
    if not variants:
        continue
    tally["generator fired"] += 1
    faults = (n.get("residual") or {}).get("faults") or {}
    iso = campaign_workers.isolate(Path("/home/grant/decomp/sbk1"), NATIVE / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    base = workspace.score(ws, iso, f"{name}_sbase", source)
    row = {"function": name, "variants": len(variants), "base_compiled": bool(base.compiled),
           "base_score": base.score, "faults": faults, "best": base.score if base.compiled else 0.0,
           "exact": None}
    for i, item in enumerate(variants):
        label, code = item[0], item[-1]
        att = workspace.score(ws, iso, f"{name}_sv{i}", code)
        tally["compiles"] += 1
        if att.compiled and workspace.repair_complete(att):
            row["exact"] = label
            (HERE / "matches").mkdir(exist_ok=True)
            (HERE / "matches" / f"{name}.c").write_text(code)
            matches.append(name)
            break
        if att.compiled and att.score > row["best"]:
            row["best"] = att.score
    tally["exact"] += bool(row["exact"])
    tally["score up"] += bool(not row["exact"] and row["best"] > (row["base_score"] if row["base_compiled"] else 0))
    with out.open("a") as stream:
        stream.write(json.dumps(row) + "\n")
    print(json.dumps({"function": name, "exact": row["exact"], "base": row["base_score"], "best": row["best"]}),
          flush=True)
print(json.dumps({**tally, "matches": matches}, indent=1))
