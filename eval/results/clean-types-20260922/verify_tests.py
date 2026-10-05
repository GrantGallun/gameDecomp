"""Capture the full project suite and compare failures with the prior-round baseline."""
import contextlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import pytest

OUT = Path(__file__).resolve().parent


class Results:
    def __init__(self):
        self.failed = []

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.append({"test": report.nodeid, "phase": report.when,
                                "failure": str(report.longrepr)})


def main():
    results = Results()
    with (OUT / "tests-full.log").open("w") as stream:
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = pytest.main(["tests", "-q", "--tb=short"], plugins=[results])
    (OUT / "tests-failures.json").write_text(json.dumps(results.failed, indent=2) + "\n")
    baseline = json.loads((OUT.parent / "clean-calls-20260922/tests-failures.json").read_text())
    before = {(row["test"], row["phase"]) for row in baseline}
    after = {(row["test"], row["phase"]) for row in results.failed}
    comparison = {"exit_code": int(code), "failed_reports": len(results.failed),
                  "new_failures": sorted(after - before), "resolved_failures": sorted(before - after),
                  "same_failure_identities": before == after}
    (OUT / "tests-comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    print(json.dumps(comparison, indent=2))


if __name__ == '__main__':
    main()
