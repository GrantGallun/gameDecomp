import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from steer import HERE, traced, insert_after_first_assignment, identity, span, units, uopt_trace  # noqa: E402

c = next(x for x in json.loads((HERE / "census.json").read_text()) if x["function"] == "waitCourseSelectRecordsClose")
name, source = c["function"], c["best_source"]
begin = source.find(name + "(")
print(source[begin - 40: begin + 1200])
base = uopt_trace.join(c["trace"]["level5"], c["trace"]["level6"])[name]
show = lambda p: sorted((identity(r), r.lr, r.adjsave, span(r), r.color) for r in p.ranges.values() if r.kind in ("M", "P"))
print("BASE", show(base))
for v in ("var_s0", "var_s1"):
    variant = insert_after_first_assignment(source, name, v, f"    if (!{v});")
    i = variant.find(f"if (!{v});")
    print("---", v, "inserted at:", repr(variant[i - 120:i + 20]))
    p = traced(name, variant)
    print("VARIANT", show(p) if p else None)
