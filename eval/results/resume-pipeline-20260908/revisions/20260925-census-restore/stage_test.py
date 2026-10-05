"""Stage the census restore into COPIES of the frozen campaign project and test it before any live change.

baseline/ = copy of the frozen project; staged/ = the same copy with staged/{eval,solver} overlaid. The frozen
suite runs on both and the failure SETS are compared: a test failing only in staged/ is a break, and the
census/ninety wiring tests (failing since the 2026-09-19 amendment) must now pass.

    python3 stage_test.py -> stage-test.json (no live file is touched)
"""
import json, re, shutil, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = HERE.parents[1] / "code"
W = Path.home() / "decomp/experiments/census-restore-20260925"
PY = str(Path.home() / "decomp/experiments/population-transfer-20260922/pytest-venv/bin/python")
FILES = ["eval/agentrepair.py", "eval/completion_campaign.py", "solver/repair_queue.py", "solver/compile_recovery.py",
         "solver/regalloc_search.py", "tests/test_repair_queue.py", "tests/test_completion_campaign.py",
         "tests/test_enabling_roots_campaign.py"]
MUST_PASS = ["tests/test_census_campaign.py", "tests/test_ninety_campaign.py", "tests/test_repair_queue.py",
             "tests/test_regalloc_campaign.py", "tests/test_frontend_fixits_campaign.py",
             "tests/test_draft_lowering_campaign.py", "tests/test_compile_chain_campaign.py",
             "tests/test_enabling_roots_campaign.py", "tests/test_completion_campaign.py"]


def copy(dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(FROZEN, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))


def pytest(project, tmp, args):
    proc = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", "--continue-on-collection-errors",
                           "--basetemp", str(tmp), *args], cwd=project,
                          env={"PYTHONPATH": str(project), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
                          capture_output=True, text=True, timeout=3000)
    return {"tail": proc.stdout.strip().splitlines()[-1:],
            "bad": sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.M)))}


def main():
    W.mkdir(parents=True, exist_ok=True)
    base, staged = W / "baseline", W / "staged"
    copy(base)
    copy(staged)
    for f in FILES:
        shutil.copy2(HERE / "staged" / f, staged / f)
    out = {"overlaid": FILES}
    out["suite_baseline"] = pytest(base, W / "tmp-base", ["tests"])
    out["suite_staged"] = pytest(staged, W / "tmp-staged", ["tests"])
    out["newly_failing_in_staged"] = sorted(set(out["suite_staged"]["bad"]) - set(out["suite_baseline"]["bad"]))
    out["fixed_in_staged"] = sorted(set(out["suite_baseline"]["bad"]) - set(out["suite_staged"]["bad"]))
    out["wiring_tests_staged"] = pytest(staged, W / "tmp-wiring", MUST_PASS)
    (HERE / "stage-test.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k not in ("overlaid",)}, indent=1)[:6000])


if __name__ == "__main__":
    main()
