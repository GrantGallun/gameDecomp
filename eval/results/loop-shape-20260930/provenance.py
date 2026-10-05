"""Provenance of every function this session made exact or function-exact: the strategy of the starting source."""
import json, sqlite3, collections
from pathlib import Path
HERE = Path(__file__).resolve().parent
RECOVERED = ("recover", "history", "provenance", "reference", "relocated-oracle", "retrodiction")


def strategy(ledger, aid):
    db = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    s = db.execute("select strategy from attempts where id=?", (aid,)).fetchone()[0] or ""
    db.close()
    return s


LED = {"campaign": "/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite", "kb": "/home/grant/decomp/kb-sbk1.sqlite"}
out = {}
fb = [json.loads(l) for l in (HERE / "rescore_fb.jsonl").read_text().splitlines()]
integ = {json.loads(l)["function"]: json.loads(l)["status"] for l in (HERE / "jtbl_integration_dry.jsonl").read_text().splitlines()}
for x in fb:
    if x.get("function_exact") and not x.get("exact"):
        out[x["name"]] = {"route": "jump-table certificate", "integration": integ.get(x["name"]), "start": strategy(x["ledger"], x["attempt_id"])}
for fname, route in (("near_frame.json", "site-edit search"), ("band_10_30_frame.json", "site-edit search")):
    frame = {f["name"]: f for f in json.loads((HERE / fname).read_text())}
    res = "near_search.jsonl" if fname == "near_frame.json" else "band_10_30_search.jsonl"
    for l in (HERE / res).read_text().splitlines():
        r = json.loads(l)
        if r.get("exact"):
            f = frame[r["function"]]
            out[r["function"]] = {"route": route, "start": strategy(LED[f["ledger"]], f["attempt_id"])}
frame = {f["name"]: f for f in json.loads((HERE / "near_left_frame.json").read_text())}
for l in (HERE / "plateau_near.jsonl").read_text().splitlines():
    r = json.loads(l)
    if r.get("exact"):
        f = frame[r["function"]]
        out[r["function"]] = {"route": "plateau search", "start": strategy(LED[f["ledger"]], f["attempt_id"])}
for v in out.values():
    v["recovered"] = any(k in v["start"] for k in RECOVERED)
(HERE / "provenance.json").write_text(json.dumps(out, indent=1))
c = collections.Counter((v["route"], v["recovered"], v.get("integration")) for v in out.values())
for k, n in sorted(c.items(), key=str):
    print(n, k)
print("not recovered:", sorted((n, v["route"], v["start"][:40]) for n, v in out.items() if not v["recovered"]))
