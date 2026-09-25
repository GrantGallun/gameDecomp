"""Fires test and reach of the new certificate stage (byte_certificate.same_addend_pairing_groups) on real objects.

For each campaign function whose best attempt scored 100 but failed the object certificate, and each binary-types
function in the same state: recompile that source in the copied workspace (recorded recipe) and certify the object
against target.o with the MAIN-TREE certificate. Also the reference's own osCreateMesgQueue in its TU (harness check).
No KB or campaign writes.

    python3 recertify.py -> recertify.json
"""
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/draft-reference-mining-20260924")
import pairs
from solver import byte_certificate as cert

HERE = Path(__file__).resolve().parent
H = Path.home() / "decomp"
mirror = pairs.mirror_repo()


def certify(name, source, stem="certprobe"):
    ws = pairs.workspace(mirror, name)
    b = pairs.build(ws, stem, source)
    if not b["compiled"]:
        return {"status": "not-compiled"}
    r = cert.certify(ws / "target.o", ws / f"{stem}.o", source=source)
    res = {"status": r["status"], "exact": r["exact"], "pairing_normalized": r.get("relocation_pairing_normalized")}
    if not r["exact"] and "target_image" in r:
        a, c = r["target_image"]["sections"], r["candidate_image"]["sections"]
        why = []
        for sec in sorted(set(a) | set(c)):
            x, y = a.get(sec), c.get(sec)
            if x is None or y is None:
                why.append(f"{sec}: only on one side")
                continue
            if {k: v for k, v in x.items() if k != "relocations"} != {k: v for k, v in y.items() if k != "relocations"}:
                why.append(f"{sec}: bytes/layout differ")
            elif sorted(map(str, x["relocations"])) != sorted(map(str, y["relocations"])):
                sx = sorted({str(t[2]) for t in x["relocations"]} - {str(t[2]) for t in y["relocations"]})
                sy = sorted({str(t[2]) for t in y["relocations"]} - {str(t[2]) for t in x["relocations"]})
                why.append(f"{sec}: relocation targets differ {sx[:2]} vs {sy[:2]}")
            elif x["relocations"] != y["relocations"]:
                why.append(f"{sec}: same records, pairing not same-addend")
        res["why"] = why
    return res


out = {}
# harness check: the reference's own definition in its TU
root = pairs.compile_root("osCreateMesgQueue")
ref_tu, _ = pairs.tu_candidate(pairs.expanded(root), "osCreateMesgQueue", None)
out["reference osCreateMesgQueue in TU"] = certify("osCreateMesgQueue", ref_tu, "certref")

camp = sqlite3.connect(f"file:{H / 'runs/resume-pipeline-20260908/campaign.sqlite'}?mode=ro", uri=True)
exact = {a for (a,) in camp.execute("select distinct func_addr from attempts where exact=1")}
cands = {}
for name, addr, source, sampling in camp.execute(
        "select f.name, a.func_addr, a.source_code, a.sampling from attempts a join functions f on f.addr=a.func_addr "
        "where a.compiled=1 and a.exact=0 and a.score=100 and a.sampling like '%object_sections_differ%'"):
    if addr not in exact and source:
        cands.setdefault(("campaign", name), source)
kb = sqlite3.connect(f"file:{H / 'kb-sbk1.sqlite'}?mode=ro", uri=True)
kb_exact = {n for (n,) in kb.execute("select distinct f.name from attempts a join functions f on f.addr=a.func_addr "
                                     "where a.exact=1")}
for name, source, sampling in kb.execute(
        "select f.name, a.source_code, a.sampling from attempts a join functions f on f.addr=a.func_addr "
        "where a.run_id like 'binary-types%' and a.compiled=1 and a.score=100 and a.exact=0 "
        "and a.sampling like '%object_sections_differ%'"):
    cands.setdefault(("binary-types", name), source)
rows = []
for (origin, name), source in sorted(cands.items()):
    res = certify(name, source)
    rows.append({"origin": origin, "function": name, **res})
    print(origin, name, res, flush=True)
import collections
out["counts"] = {o: {f"{k[0]}|normalized={k[1]}": v for k, v in collections.Counter(
                     (r["status"], r.get("pairing_normalized")) for r in rows if r["origin"] == o).items()}
                 for o in ("campaign", "binary-types")}
out["why_not"] = dict(collections.Counter(w.split(":")[1].strip()[:40] for r in rows for w in r.get("why", [])))
out["now_exact_functions"] = sorted({r["function"] for r in rows if r.get("exact")})
out["rows"] = rows
(HERE / "recertify.json").write_text(json.dumps(out, indent=1, default=str))
print(json.dumps({k: v for k, v in out.items() if k != "rows"}, indent=1, default=str))
