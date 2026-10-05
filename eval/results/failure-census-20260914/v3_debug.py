"""Debug schema 3 refusals on specific functions: dump target/candidate data and relocation groups.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/failure-census-20260914/v3_debug.py NAME [SOURCE]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import byte_certificate as cert, function_boundary as fb, workspace  # noqa: E402

name = sys.argv[1]
pending = {r["function"]: r for r in json.loads((HERE / "pending.json").read_text())}
source = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(pending[name]["source"])
bench = regalloc_probe.Bench({"name": name, "source": str(source)})
try:
    attempt = workspace.score(bench.ws, bench.isolated, "dbg", source.read_text(), conn=bench.conn, func=name)
    ws = bench.ws
    for label, path in (("target", ws / "target.o"), ("candidate", ws / "dbg.o")):
        data = path.read_bytes()
        sections = cert.object_image(data)["sections"]
        print(label, {k: v["size"] for k, v in sections.items()})
        print("  text relocs", sections[".text"]["relocations"])
        for section in set(sections) & fb.DATA_SECTIONS:
            print("  ", section, fb._section_bytes(data, section).hex())
        print("  data symbols", fb._data_symbols(data))
    asm = (ws / "target.s").read_text()
    print("annotated", fb.DATA_LABEL.findall(asm))
    boundary = (attempt.verification or {}).get("function_boundary") or {}
    print("boundary", boundary.get("status"), boundary.get("error"), boundary.get("schema_3_error"))
    print(attempt.diff[:1500])
finally:
    bench.close()
