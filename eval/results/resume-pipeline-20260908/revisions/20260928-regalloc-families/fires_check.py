"""Silent-decline check in the STAGED FROZEN tree: every mechanism this amendment ships fires there.

Unit tests stub the compiler and run against main; a missing or incompatible module in the frozen tree
could make a family decline silently (regalloc_mutations swallows per-family exceptions by design). Rows:
  key          the real optimizer key returns a key, IDO-invisible respelling = same key, a variant differs
  coalesce     scalar_coalesce proposes `temp_h0->temp_v0` on stepRaceMotionLoopingAnimation's root
  scoped       scoped_field proposes a `temp_v0_2` elimination on the AerialTrick fixture
  wired        regalloc_mutations.variants(..., coalesce=True) yields both families
Each row has "ok". apply_amendment.py refuses unless every row is ok.

    PYTHONPATH=<staged project> python fires_check.py <staged project>
"""
import glob
import json
import sys
from pathlib import Path

project = Path(sys.argv[1])
sys.path.insert(0, str(project))
from eval import campaign_workers  # noqa: E402
from solver import ido_stages, regalloc_mutations, scalar_coalesce, scoped_field  # noqa: E402

for module in (ido_stages, regalloc_mutations, scalar_coalesce, scoped_field):
    assert Path(module.__file__).resolve().is_relative_to(project.resolve()), module.__file__
rows = []

# key
cohort = json.loads(Path("/mnt/c/Code/gameDecomp/eval/results/regalloc-keyed-20260927/cohort.json").read_text())
for row in cohort["functions"][:2]:
    name, source = row["name"], Path(row["source"]).read_text()
    iso = campaign_workers.isolate(Path("/home/grant/decomp/sbk1"),
                                   Path("/home/grant/decomp/experiments/regalloc-families-stage-20260928/fires") / name, name)
    ws = iso / "nonmatchings" / name
    base = ido_stages.optimizer_key(iso, ws, name, source)
    blank = ido_stages.optimizer_key(iso, ws, name, source.replace("{\n", "{\n\n", 1))
    other = {ido_stages.optimizer_key(iso, ws, name, v)
             for _l, _k, v in list(regalloc_mutations.variants(source, name, ""))[:12]}
    rows.append({"check": "key", "function": name,
                 "ok": base is not None and blank == base and any(k and k != base for k in other)})

# coalesce: the motivating root, read from the first trial's retained events (no reference involved)
root = None
for p in glob.glob("/home/grant/decomp/experiments/evolvability-*-20260928/run-*/t*/0/production/result.json"):
    r = json.loads(Path(p).read_text())
    if r["function"] == "stepRaceMotionLoopingAnimation":
        root = r["events"][0]["source"]
        break
labels = [v[0] for v in scalar_coalesce.variants(root, "stepRaceMotionLoopingAnimation")] if root else []
rows.append({"check": "coalesce", "labels": labels[:4],
             "ok": any("temp_h0->temp_v0" in label for label in labels)})

# scoped_field: the motivating fixture shipped with the tests
fixture = (project / "tests/fixtures/scoped_field_aerial.c").read_text()
scoped = [v[0] for v in scoped_field.variants(fixture, "updateRacePlayerMode48AerialTrick")]
rows.append({"check": "scoped", "labels": scoped[:4], "ok": any("temp_v0_2" in label for label in scoped)})

# wired: both families through the generator the campaign's register search calls
families = {v[1] for v in regalloc_mutations.variants(root, "stepRaceMotionLoopingAnimation", "", coalesce=True)} \
    | {v[1] for v in regalloc_mutations.variants(fixture, "updateRacePlayerMode48AerialTrick", "", coalesce=True)}
rows.append({"check": "wired", "families": sorted(f for f in families if "coalesce" in f or "scoped" in f),
             "ok": any("coalesce" in f for f in families) and any("scoped" in f for f in families)})
print(json.dumps(rows, indent=1))
if not all(r["ok"] for r in rows):
    raise SystemExit("a shipped mechanism does not fire in the staged frozen tree")
