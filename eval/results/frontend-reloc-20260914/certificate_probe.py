import json, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import regalloc_probe
from solver import workspace, byte_certificate
rows = {r["name"]: r for r in json.loads(Path("eval/results/frontend-reloc-20260914/relocation-targets.json").read_text())["functions"]}
for name in sys.argv[1:]:
    bench = regalloc_probe.Bench(rows[name])
    try:
        source = Path(rows[name]["source"]).read_text()
        att = workspace.score(bench.ws, bench.isolated, "base", source, conn=bench.conn, func=name)
        v = byte_certificate.certify(bench.ws / "target.o", bench.ws / "base.o", source=source, build_inputs={})
        t, c = v["target_image"]["sections"], v["candidate_image"]["sections"]
        diffs = {s: {k: (t.get(s, {}).get(k), c.get(s, {}).get(k)) for k in set(t.get(s, {})) | set(c.get(s, {}))
                     if t.get(s, {}).get(k) != c.get(s, {}).get(k) and k != "relocations"} for s in set(t) | set(c) if t.get(s) != c.get(s)}
        reloc = {s: ([r for r in t.get(s, {}).get("relocations", []) if r not in c.get(s, {}).get("relocations", [])][:3],
                     [r for r in c.get(s, {}).get("relocations", []) if r not in t.get(s, {}).get("relocations", [])][:3]) for s in set(t) | set(c)}
        print(name, "certificate exact:", v["exact"], "status:", v.get("status"), "score:", att.score)
        print("   section diffs:", json.dumps(diffs)[:500])
        print("   relocation diffs:", json.dumps({s: r for s, r in reloc.items() if r[0] or r[1]})[:500])
    finally:
        bench.close()
