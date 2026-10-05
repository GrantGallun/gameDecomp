"""Does placeholder_declarations see a header #define? (bench only): python header_macro_check.py"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from eval import regalloc_probe  # noqa: E402
from solver import placeholder_declarations as pd, project_headers  # noqa: E402

function = "drawCharacterSelectCoursePlayerStatsPanel"
row = next(r for r in json.loads((HERE / "not_compiled.json").read_text()) if r["function"] == function)
source = Path(row["source"]).read_text()
bench = regalloc_probe.Bench({"name": function, "source": row["source"]})
try:
    headers = pd.header_names(bench.isolated, source)
    print("in masked headers:", "gCharacterSelectCourseOptionsByUnlock" in headers, "header chars", len(headers))
    raw = "#define gX (y.z)\nextern int w;\n"
    print("mask keeps define:", repr(project_headers._mask_noncode(raw)))
    print([p for p in pd.propose(source, function, headers)[1]["placeholders"]])
finally:
    bench.close()
