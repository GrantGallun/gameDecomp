"""Stage the load_modify_stores linear-gap hotfix over the frozen campaign runtime.

    python eval/results/regalloc-hotfix-20260914/stage.py

The deployed generator matched the statements between a load and its update with
`(?:[ \\t]*[^;\\n]*;[ \\t]*\\n?)*?`. Its overlapping quantifiers backtrack exponentially
when no update follows. Offline dominant-1 spun for 4.5 CPU hours, and 17 of 454
queued regalloc_search campaign nodes spin in it (eval/results/regalloc-20260913/hang-exposure.json).
Workers have no job timeout, so a few such dispatches would stall every slot.
The main-tree module differs from live only by the linear replacement.
"""
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / "eval/results/resume-pipeline-20260908/code"
STAGE = OUT / "staged-code"

shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "results"))
(STAGE / "eval/results").symlink_to(ROOT / "eval/results", target_is_directory=True)
manifest = {}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


rel = "solver/regalloc_mutations.py"
shutil.copy2(ROOT / rel, STAGE / rel)
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel),
                 "scope": "load_modify_stores: linear statement-gap scan replaces a backtracking regex"}

rel = "tests/test_regalloc_mutations.py"
main_tests = (ROOT / rel).read_text()
start = main_tests.index("def test_load_modify_store_skips_intervening_statements_and_stays_linear():")
end = main_tests.index("\n\n\n", start)
(STAGE / rel).write_text((LIVE / rel).read_text().rstrip("\n") + "\n\n\n" + main_tests[start:end] + "\n")
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel),
                 "scope": "fire-and-linearity test for the gap scan"}

(OUT / "staged-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"stage": str(STAGE), "files": list(manifest)}))
