"""Silent-decline check: in the STAGED FROZEN tree (not main), the real optimizer key returns a key for a
real campaign function, a respelling IDO flattens gets the same key, and a real change gets a different one.
Every unit test stubs the key, and search() swallows key exceptions by design, so without this a missing
import in the frozen tree would disable keying with no error.

    PYTHONPATH=<staged project> python fires_check.py <staged project>
"""
import json
import sys
from pathlib import Path

project = Path(sys.argv[1])
sys.path.insert(0, str(project))
from eval import campaign_workers  # noqa: E402
from solver import ido_stages  # noqa: E402

assert Path(ido_stages.__file__).resolve().is_relative_to(project.resolve()), ido_stages.__file__
cohort = json.loads(Path("/mnt/c/Code/gameDecomp/eval/results/regalloc-keyed-20260927/cohort.json").read_text())
report = []
for row in cohort["functions"][:3]:
    name = row["name"]
    source = Path(row["source"]).read_text()
    iso = campaign_workers.isolate(Path("/home/grant/decomp/sbk1"),
                                   Path("/home/grant/decomp/experiments/regalloc-keyed-stage-20260928/fires") / name,
                                   name)
    ws = iso / "nonmatchings" / name
    base = ido_stages.optimizer_key(iso, ws, name, source)
    blank = ido_stages.optimizer_key(iso, ws, name, source.replace("{\n", "{\n\n", 1))   # IDO-invisible
    from solver import regalloc_mutations
    keys = set()
    for _label, _kind, variant in list(regalloc_mutations.variants(source, name, ""))[:12]:
        keys.add(ido_stages.optimizer_key(iso, ws, name, variant))
    report.append({"function": name, "key": base is not None,
                   "respelling same key": base is not None and blank == base,
                   "some variant gets a different key": any(k is not None and k != base for k in keys)})
print(json.dumps(report, indent=1))
if not all(r["key"] and r["respelling same key"] and r["some variant gets a different key"] for r in report):
    raise SystemExit("keying does not fire in the staged frozen tree")
