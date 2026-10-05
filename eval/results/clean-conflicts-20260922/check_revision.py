"""Check the review adjustment on every exact input to the changed action."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.dev_set_export import recover_source
from eval.intake_probe import SEQUENCE
from solver import compile_recovery

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / "decomp/experiments/clean-conflicts-20260922"
paired = json.loads((OUT / "paired.json").read_text())
assert len(paired["rows"]) == paired["expected"] == 200, "wait for complete paired replay"
conn = sqlite3.connect(f"file:{NATIVE / 'attempts.sqlite'}?mode=ro", uri=True)
namespace = dict(compile_recovery.__dict__)
previous_text = (OUT / "header-after.py.txt").read_text()
exec(previous_text, namespace)
previous = namespace["header_variant"]
changed = []
rows = []
index = SEQUENCE.index("eval.intake_runners.header_variant")
for row in paired["rows"]:
    digest = row["draft_sha256"]
    for step in row["arms"]["before"]["trace"]:
        if SEQUENCE.index(step["action"]) >= index:
            break
        if step["adopted"]:
            digest = step["sha256"]
    source, lineage = recover_source(conn, digest)
    assert source is not None, (row["function"], lineage)
    name = row["function"]
    repo = NATIVE / "builds" / name
    ws = repo / "nonmatchings" / name
    target = json.loads((ws / ".compiler-target.json").read_text())["target"]
    args = (repo, name, (ws / "target.s").read_text(), source, target)
    a, ar = previous(*args)
    b, br = compile_recovery.header_variant(*args)
    if a != b:
        changed.append(name)
    rows.append({"function": name, "input_sha256": digest, "same_source": a == b,
                 "same_report": ar == br})
report = {"rows": rows, "changed": changed,
          "previous_sha256": hashlib.sha256(previous_text.encode()).hexdigest(),
          "final_module_sha256": hashlib.sha256(Path(compile_recovery.__file__).read_bytes()).hexdigest()}
(OUT / "review-adjustment.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps({"states": len(rows), "changed": changed}))
assert not changed, "changed candidates require another compiler replay"
