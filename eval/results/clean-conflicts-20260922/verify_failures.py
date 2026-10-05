"""Check existing suite failures with and without this task's header change."""
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pytest
from solver import compile_recovery

OUT = Path(__file__).resolve().parent
arm = sys.argv[1]
if arm == "before":
    namespace = dict(compile_recovery.__dict__)
    exec((OUT / "header-before.py.txt").read_text(), namespace)
    compile_recovery.header_variant = namespace["header_variant"]


class Results:
    def __init__(self):
        self.failed = []

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.append({"test": report.nodeid, "phase": report.when,
                                "failure": str(report.longrepr)})


results = Results()
with (OUT / f"tests-{arm}-existing-failures.log").open("w") as stream:
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        code = pytest.main(["tests", "--lf", "-q", "--tb=short"], plugins=[results])
(OUT / f"tests-{arm}-existing-failures.json").write_text(json.dumps(results.failed, indent=2) + "\n")
print(json.dumps({"arm": arm, "exit_code": int(code), "failed_reports": len(results.failed),
                  "tests": sorted({row["test"] for row in results.failed})}, indent=2))
