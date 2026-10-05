"""Stage the guard_before_load linear-body hotfix over the frozen campaign runtime.

    python eval/results/regalloc-hotfix2-20260914/stage.py

`(?:(?P=i)[ \\t]+[^\\n]*\\n)*?` let both quantifiers claim a guard line's leading spaces and
backtracked exponentially when no closing brace matched. A campaign worker spun on
osEPiRawStartDma for over an hour (2026-09-14); spin_audit.py found 24 pending sources that
spin on the live generator and none on the fix across all 1070 pending sources.
"""
import hashlib
import json
from pathlib import Path
import shutil

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
LIVE = ROOT / "eval/results/resume-pipeline-20260908/code"
STAGE = OUT / "staged-code"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


shutil.copytree(LIVE, STAGE, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "results"))
(STAGE / "eval/results").symlink_to(ROOT / "eval/results", target_is_directory=True)
manifest = {}
rel = "solver/regalloc_mutations.py"
shutil.copy2(ROOT / rel, STAGE / rel)
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel),
                 "scope": "guard_before_load: unambiguous guard-body line pattern"}
rel = "tests/test_regalloc_mutations.py"
main_tests = (ROOT / rel).read_bytes().decode().replace("\r\n", "\n")
start = main_tests.index("def test_guard_before_load_stays_linear_on_an_unclosed_guard():")
test = main_tests[start:].rstrip("\n") + "\n"
(STAGE / rel).write_text((LIVE / rel).read_text().rstrip("\n") + "\n\n\n" + test)
manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel), "scope": "linearity test"}
(OUT / "staged-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"stage": str(STAGE), "files": list(manifest)}))
