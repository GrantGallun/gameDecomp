"""Why the ROM-backed function-extent certificate refuses the score-100 relocation functions.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/frontend-reloc-20260914/boundary_probe.py

For the literal (baseline) and extern (relocation_names) source of each function:
function_boundary status/error, object sections, and .text relocations whose
identity differs between target and candidate. Writes boundary-probe.json.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eval import regalloc_probe  # noqa: E402
from solver import byte_certificate, function_boundary, workspace  # noqa: E402

rows = {r["name"]: r for r in json.loads((HERE / "relocation-targets.json").read_text())["functions"]}
summary = json.loads((HERE / "relocation-probe-only/summary.json").read_text())
names = [r["function"] for r in summary if (r.get("best") or {}).get("score") == 100.0]
report = []
for name in names:
    bench = regalloc_probe.Bench(rows[name])
    try:
        meta = bench.conn.execute("SELECT addr,size FROM functions WHERE name=?", (name,)).fetchone()
        entry = {"function": name}
        for label, source in (("literal", Path(rows[name]["source"]).read_text()),
                              ("extern", (HERE / "relocation-probe-only" / f"{name}.c").read_text())):
            tag = f"boundary_{label}"
            att = workspace.score(bench.ws, bench.isolated, tag, source, conn=bench.conn, func=name)
            target = byte_certificate.object_image((bench.ws / "target.o").read_bytes())["sections"]
            candidate = byte_certificate.object_image((bench.ws / f"{tag}.o").read_bytes())["sections"]
            boundary = function_boundary.certify(
                target=bench.ws / "target.o", candidate=bench.ws / f"{tag}.o", assembly=bench.ws / "target.s",
                rom=bench.isolated / "snowboardkids.z64", config=bench.isolated / "snowboardkids.yaml",
                symbols=bench.isolated / "symbol_addrs.txt", function=name, address=meta[0], size=meta[1])
            left = target.get(".text", {}).get("relocations", [])
            right = candidate.get(".text", {}).get("relocations", [])
            entry[label] = {
                "score": att.score, "exact": att.exact, "boundary_status": boundary.get("status"),
                "boundary_error": boundary.get("error"),
                "target_sections": {s: v.get("size") for s, v in target.items()},
                "candidate_sections": {s: v.get("size") for s, v in candidate.items()},
                "relocations_only_target": [r for r in left if r not in right][:6],
                "relocations_only_candidate": [r for r in right if r not in left][:6]}
        report.append(entry)
        print(json.dumps({"function": name, **{k: (entry[k]["boundary_error"], entry[k]["target_sections"],
                                                    entry[k]["candidate_sections"]) for k in ("literal", "extern")}}),
              flush=True)
    finally:
        bench.close()
(HERE / "boundary-probe.json").write_text(json.dumps(report, indent=1))
