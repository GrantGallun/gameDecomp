"""Run scoped regressions and record the tested module identities."""
import hashlib
import json
import os
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
frozen = json.loads((OUT / "freeze.json").read_text())
CODE = ROOT if os.name == "nt" else Path(frozen["code_root"])
sys.path.insert(0,str(CODE))
import pytest

TESTS = ["repair_theory","theory_repairs","theory_planner","repair_graph","repair_rules","repair_transitions","repair_planner","storage_repairs",
         "search_scheduler","search_replay","search_evolution","search_pilot",
         "regalloc_mutations","regalloc_search","tool_boundary","tool_registry"]
MODULES = ["solver.repair_theory","solver.theory_repairs","eval.theory_planner","eval.repair_graph","eval.repair_transitions","eval.repair_planner",
           "eval.search_replay","solver.repair_rules"]


class Report:
    def __init__(self):
        self.outcomes = {"passed":0,"failed":0,"skipped":0}
    def pytest_runtest_logreport(self,report):
        if report.when == "call" or report.failed:
            self.outcomes[report.outcome] += 1


def main():
    plugin = Report()
    result = pytest.main(["-q",*[str(ROOT / "tests" / f"test_{name}.py") for name in TESTS]],plugins=[plugin])
    module_hashes = {}
    for name in MODULES:
        path = Path(sys.modules[name].__file__).resolve()
        assert path.is_relative_to(CODE.resolve())
        relative = path.relative_to(CODE).as_posix()
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == frozen["files"][relative]
        module_hashes[relative] = actual
    report = {"platform":sys.platform,"python":sys.version,"code_root":str(CODE),
              "tests":TESTS,"exit_code":int(result),"outcomes":plugin.outcomes,"modules":module_hashes}
    (OUT / f"tests-{sys.platform}.json").write_text(json.dumps(report,indent=2))
    raise SystemExit(result)


if __name__ == "__main__":
    main()
