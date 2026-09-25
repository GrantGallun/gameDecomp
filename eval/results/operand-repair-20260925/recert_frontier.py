"""Re-certify every campaign compiled-not-exact best source under the current certificate (strict / same-addend HI-LO
pairing / rodata values). One compile per function through the official scorer into the private DB; no search.
Rows go to E/recert/; exact-and-complete ones are then recorded with `record.py --rows recert`.

    python3 recert_frontier.py [--jobs 4] -> recert-summary.json
"""
import collections, concurrent.futures, json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import operand_repair as o

HERE = Path(__file__).resolve().parent


def one(name, source, campaign_score, db):
    try:
        v = o.Scorer(name, db)(source, "recert-baseline")
    except Exception as exc:
        return {"function": name, "status": "harness-error", "error": repr(exc)[-200:]}
    status = ("exact-at-baseline" if v.get("complete") else "object-exact-gate-refused" if v.get("exact")
              else "not-exact" if v.get("compiled") else "not-compiled")
    return {"function": name, "status": status, "campaign_score": campaign_score, "score": v.get("score"),
            "source": source if v.get("complete") else None}


def main():
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 4
    frontier = json.loads((HERE.parent / "frontier-20260924/frontier.json").read_text())["rows"]
    done_pool = {p.stem for p in (o.E / "rows").glob("*.json")}
    camp = sqlite3.connect(f"file:{o.CAMP}?mode=ro", uri=True)
    db = o.private_db()
    out_dir = o.E / "recert"
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [r for r in frontier if r["function"] not in done_pool and not (out_dir / f"{r['function']}.json").exists()]
    srcs = {r["function"]: camp.execute("select source_code from attempts where id=?", (r["attempt"],)).fetchone()[0]
            for r in todo}
    print(len(frontier), "frontier;", len(todo), "to recertify", flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        futs = [ex.submit(one, r["function"], srcs[r["function"]], r["score"], db) for r in todo]
        for i, f in enumerate(concurrent.futures.as_completed(futs), 1):
            res = f.result()
            (out_dir / f"{res['function']}.json").write_text(json.dumps(res))
            if res["status"] == "exact-at-baseline" or i % 100 == 0:
                print(i, res["function"], res["status"], flush=True)
    rows = [json.loads(p.read_text()) for p in out_dir.glob("*.json")]
    s = {"recertified": len(rows), "status": dict(collections.Counter(r["status"] for r in rows)),
         "exact": sorted(r["function"] for r in rows if r["status"] == "exact-at-baseline")}
    (HERE / "recert-summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps({k: v for k, v in s.items() if k != "exact"}, indent=1))


if __name__ == "__main__":
    main()
