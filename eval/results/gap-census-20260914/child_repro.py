"""Feed a real unparseable model child (from the attempt log, read-only) through modelrepair.search normalization.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/gap-census-20260914/child_repro.py FUNCTION ATTEMPT_ID [--main]

Mocks compilation (no toolchain) so only the normalization passes run. Prints the traceback if one escapes.
Writes child-<attempt>.c for use as a test fixture.
"""
import sqlite3
import sys
import traceback
from pathlib import Path

RUN = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/c/Code/gameDecomp" if "--main" in sys.argv else str(RUN / "code"))
from solver import modelrepair, workspace  # noqa: E402

function, attempt_id = sys.argv[1], int(sys.argv[2])
conn = sqlite3.connect(f"file:{RUN / 'campaign.sqlite'}?mode=ro", uri=True)
source = conn.execute("SELECT source_code FROM attempts WHERE id=?", (attempt_id,)).fetchone()[0]
(HERE / f"child-{attempt_id}.c").write_text(source)
root = workspace.Attempt(False, 0, False, "", "Syntax Error", "", attempt_id, frontend={"passed": False, "diagnostics": "error"})
workspace.target_asm = lambda *a: "glabel f\njr ra\nnop"
workspace.assert_uncontaminated = lambda *a: None
workspace.score = lambda *a, **k: root
try:
    result = modelrepair.search(HERE, function, source, HERE, model="test", endpoint="none", base_attempt=root,
                                resilient=True, max_calls=0)
    print("completed; log tail:", result.log[-3:])
except Exception:
    traceback.print_exc()
