"""Stage the derived pointer-contract amendment over the frozen campaign runtime.

    python eval/results/pointer-contracts-20260914/stage.py

Semantic-lane stagnation (2026-09-14): 185 of 200 semantic model jobs since the last amendment
were ±0, and 66 of 263 semantic-lane nodes failed only because an opaque callee received a
local buffer at a different frame offset. solver/pointer_contracts.py derives, from each
opaque callee's ROM-verified extracted instructions, which bytes it reads and writes through
pointer arguments; eval/semantic_lane.Panel adds those contracts to its callee environment.
Paired probe (census-deploy-20260914/pointer-contract-probe-*.json): observed failures became
passes or inconclusive with no passing control lost.
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
for rel, scope in (("solver/pointer_contracts.py", "per-argument pointer contracts from ROM-verified callee instructions"),
                   ("eval/semantic_lane.py", "panel callee environment adds derived pointer contracts"),
                   ("tests/test_pointer_contracts.py", "fire, decline and label tests")):
    shutil.copy2(ROOT / rel, STAGE / rel)
    manifest[rel] = {"old_sha256": sha(LIVE / rel), "new_sha256": sha(STAGE / rel), "scope": scope}
(OUT / "staged-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps({"stage": str(STAGE), "files": list(manifest)}))
