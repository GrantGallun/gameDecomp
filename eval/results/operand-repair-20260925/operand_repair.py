"""Operand repair with full evidence on the 130 structurally-correct campaign functions (PROTOCOL.md).

    python3 operand_repair.py [--jobs 4] [--limit N]   -> E/rows/<fn>.json, summary.json here
    python3 operand_repair.py --record                  -> record exact results into the production KB
"""
import collections
import json
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

ROOT = Path("/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate  # noqa: E402
from eval.tool_agent_run import _attempt_to_verdict  # noqa: E402
from solver import diffrepair, regalloc_mutations, rodata_symbol, workspace  # noqa: E402

HERE = Path(__file__).resolve().parent
H = Path.home() / "decomp"
E = H / "experiments/operand-repair-20260925"
REPO = H / "sbk1"
KB = H / "kb-sbk1.sqlite"
CAMP = H / "runs/resume-pipeline-20260908/campaign.sqlite"
PER_STEP, BUDGET, RESTART, RESTARTS = 8, 40, 16, 2
_db_lock = threading.Lock()


def private_db() -> Path:
    path = E / "private.sqlite"
    if path.exists():
        return path
    E.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    conn.execute("ATTACH DATABASE ? AS kb", (f"file:{KB}?mode=ro",))
    conn.execute("INSERT INTO tus SELECT * FROM kb.tus")
    conn.execute("INSERT INTO functions SELECT * FROM kb.functions")
    conn.commit()
    conn.close()
    return path


class Scorer:
    def __init__(self, name, db_path):
        self.name = name
        self.repo = isolate(REPO, E / "iso" / name, name)
        self.ws = self.repo / "nonmatchings" / name
        self.db_path = db_path

    def __call__(self, source, label):
        conn = sqlite3.connect(self.db_path, timeout=300)
        try:
            with _db_lock:
                pass
            att = workspace.score(self.ws, self.repo, self.name, source, conn=conn, func=self.name,
                                  strategy=f"operand-repair-search:{label}", run_id="operand-repair-20260925")
            conn.commit()
        finally:
            conn.close()
        v = _attempt_to_verdict(att)
        v["complete"] = workspace.repair_complete(att)
        return v


ELF = REPO / "build/snowboardkids.elf"
MAP_TEXT = (REPO / "build/snowboardkids.map").read_text(errors="replace")


def proposals(source, name, verdict, fired, target_obj=None):
    out = []
    try:                                                   # the rodata-literal owner (solver/rodata_symbol.py)
        for label, cand in rodata_symbol.variants(source, name, verdict.get("diff") or "",
                                                  verdict.get("source_attribution"), elf=ELF, map_text=MAP_TEXT,
                                                  target_obj=target_obj):
            out.append((label, "rodata_symbol", cand))
    except Exception:
        fired["rodata_symbol-crash"] += 1
    try:
        for label, kind, cand in regalloc_mutations.variants(source, name, verdict.get("diff") or "", evidence=verdict):
            out.append((label, kind, cand))
    except Exception as exc:                              # a family crash declines this step; recorded
        fired["stream-decline"] += 1
    try:
        code, changed, info = diffrepair.repair(source, verdict.get("diff") or "")
        if changed and code != source:
            out.insert(len([o for o in out if o[1] == "rodata_symbol"]), ("diffrepair", "diffrepair", code))
    except Exception:
        fired["diffrepair-crash"] += 1
    return out


def climb(score, name, source, verdict, budget, seen, log, fired):
    target_obj = score.ws / "target.o"
    best_src, best, used = source, verdict, 0
    while used < budget and not best.get("complete"):
        kids = []
        for label, kind, cand in proposals(best_src, name, best, fired, target_obj):
            if cand in seen:
                continue
            seen.add(cand)
            kids.append((label, kind, cand))
            fired[kind] += 1
            if len(kids) >= PER_STEP:
                break
        if not kids:
            break
        step = None
        for label, kind, cand in kids:
            if used >= budget:
                break
            v = score(cand, f"{kind}:{label}"[:120])
            used += 1
            if not v.get("compiled"):
                continue
            key = (v.get("complete"), v.get("exact"), v.get("score") or 0)
            if step is None or key > step[0]:
                step = (key, cand, v, label, kind)
        if step is None or step[0] <= (best.get("complete"), best.get("exact"), best.get("score") or 0):
            break
        best_src, best = step[1], step[2]
        log.append({"kind": step[4], "label": step[3][:80], "score": best.get("score"), "exact": best.get("exact"),
                    "complete": best.get("complete")})
    return best_src, best, used


def one(name, source, campaign_score, census_class, db_path):
    fired = collections.Counter()
    row = {"function": name, "census": census_class, "campaign_score": campaign_score}
    try:
        score = Scorer(name, db_path)
        base = score(source, "baseline")
    except Exception as exc:
        return row | {"status": "harness-error", "error": repr(exc)[-300:]}
    row["baseline"] = base.get("score")
    if not base.get("compiled"):
        return row | {"status": "baseline-not-compiled"}
    row["reproduced"] = abs((base.get("score") or 0) - campaign_score) <= 0.01
    if base.get("complete"):
        return row | {"status": "exact-at-baseline", "source": source}
    log, seen = [], {source}
    src, best, used = climb(score, name, source, base, BUDGET, seen, log, fired)
    for _ in range(RESTARTS):
        if best.get("complete"):
            break
        log.append({"restart": True})
        src, best, u = climb(score, name, src, best, RESTART, seen, log, fired)
        used += u
    status = ("exact" if best.get("complete") else
              "object-exact-gate-refused" if best.get("exact") else
              "improved" if (best.get("score") or 0) > (base.get("score") or 0) else "flat")
    return row | {"status": status, "end": best.get("score"), "compiles": used, "path": log, "fired": dict(fired),
                  "source": src if best.get("exact") else None}


def main():
    import concurrent.futures
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    census = json.loads((HERE.parent / "alloc-census-20260924/census.json").read_text())
    fronts = json.loads((HERE.parent / "frontier-20260924/fronts.json").read_text())
    frontier = {r["function"]: r for r in json.loads((HERE.parent / "frontier-20260924/frontier.json").read_text())["rows"]}
    cls = {r["function"]: r.get("first", r["status"]) for r in census["rows"]}
    lo, hi = (int(x) for x in (sys.argv[sys.argv.index("--band") + 1].split("-") if "--band" in sys.argv else ("0", "0")))
    pool = [r["function"] for r in fronts["rows"] if lo <= r["structural"] <= hi and r["size"] in ("small", "medium")]
    order = {"none": 0, "ugen_temp": 1, "split": 2, "selection": 3}
    pool.sort(key=lambda n: order.get(cls.get(n), 4))
    if "--limit" in sys.argv:
        pool = pool[:int(sys.argv[sys.argv.index("--limit") + 1])]
    db_path = private_db()
    camp = sqlite3.connect(f"file:{CAMP}?mode=ro", uri=True)
    (E / "rows").mkdir(parents=True, exist_ok=True)
    todo = [n for n in pool if not (E / "rows" / f"{n}.json").exists()]
    srcs = {n: camp.execute("select source_code from attempts where id=?", (frontier[n]["attempt"],)).fetchone()[0]
            for n in todo}
    print(len(pool), "pool;", len(todo), "to run", flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool_ex:
        futs = {pool_ex.submit(one, n, srcs[n], frontier[n]["score"], cls.get(n, "?"), db_path): n for n in todo}
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            (E / "rows" / f"{r['function']}.json").write_text(json.dumps(r))
            print(r["function"], r["census"], r["status"], r.get("baseline"), "->", r.get("end"), flush=True)
    rows = [json.loads((E / "rows" / f"{n}.json").read_text()) for n in pool]
    fired = collections.Counter()
    for r in rows:
        fired.update(r.get("fired", {}))
    s = {"pool": len(pool), "status": dict(collections.Counter(r["status"] for r in rows)),
         "by_census": {c: dict(collections.Counter(r["status"] for r in rows if r["census"] == c))
                       for c in sorted({r["census"] for r in rows})},
         "reproduced": sum(bool(r.get("reproduced")) for r in rows),
         "families_on_exact_paths": dict(collections.Counter(s["kind"] for r in rows if r["status"] in ("exact",
                                         "object-exact-gate-refused") for s in r.get("path", []) if "kind" in s)),
         "proposals_fired": dict(fired.most_common()),
         "exact": sorted(r["function"] for r in rows if r["status"] in ("exact", "exact-at-baseline"))}
    (HERE / ("summary.json" if (lo, hi) == (0, 0) else f"summary-band{lo}-{hi}.json")).write_text(json.dumps(s, indent=1))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
