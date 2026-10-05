"""Stage the solver refresh into COPIES of the frozen campaign project and test it before any live change.

1. baseline/ = copy of the frozen project; staged/ = the same copy with the closure's changed solver files overlaid
   from the main tree (closure.json).
2. The frozen project's own suite on both; the failure SETS are compared: a test failing only in staged/ is a
   compatibility break.
3. The main tree's tests for the refreshed modules, run against staged/.
4. Import every frozen eval/*.py module against staged/.

    python3 stage_test.py -> stage-test.json (no live file is touched)
"""
import json, re, shutil, subprocess, sys
from pathlib import Path

MAIN = Path("/mnt/c/Code/gameDecomp")
FROZEN = MAIN / "eval/results/resume-pipeline-20260908/code"
HERE = Path(__file__).resolve().parent
W = Path.home() / "decomp/experiments/solver-refresh-20260925"
PY = str(Path.home() / "decomp/experiments/population-transfer-20260922/pytest-venv/bin/python")
MAIN_TESTS = ["test_branch_shape.py", "test_byte_certificate.py", "test_rodata_symbol.py", "test_regalloc_mutations.py",
              "test_regalloc_search.py", "test_evidence_site.py", "test_source_attribution.py",
              "test_frontend_type_repair.py", "test_owner_rewrites.py", "test_storage_repairs.py",
              "test_representation_repairs.py", "test_strength_inverse.py", "test_edit_locality.py", "test_family_gates.py"]


def copy(dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(FROZEN, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))


def suite(project, tmp):
    # --continue-on-collection-errors: 4 frozen test modules need fixtures outside the snapshot (identical on both
    # copies); without it pytest aborts the whole run and the comparison is vacuous (found in the first run).
    proc = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", "--continue-on-collection-errors",
                           "--basetemp", str(tmp), "tests"],
                          cwd=project, env={"PYTHONPATH": str(project), "PATH": "/usr/bin:/bin",
                                            "HOME": str(Path.home())}, capture_output=True, text=True, timeout=3000)
    bad = sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.M)))
    return {"tail": proc.stdout.strip().splitlines()[-1:], "bad": bad}


def main():
    closure = json.loads((HERE / "closure.json").read_text())
    W.mkdir(parents=True, exist_ok=True)
    base, staged = W / "baseline", W / "staged"
    copy(base)
    copy(staged)
    for m in closure["changed"]:
        shutil.copy2(MAIN / "solver" / f"{m}.py", staged / "solver" / f"{m}.py")
    out = {"overlaid": closure["changed"]}
    out["frozen_suite_baseline"] = suite(base, W / "tmp-base")
    out["frozen_suite_staged"] = suite(staged, W / "tmp-staged")
    out["newly_failing_in_staged"] = sorted(set(out["frozen_suite_staged"]["bad"]) - set(out["frozen_suite_baseline"]["bad"]))
    out["fixed_in_staged"] = sorted(set(out["frozen_suite_baseline"]["bad"]) - set(out["frozen_suite_staged"]["bad"]))
    tests = [str(MAIN / "tests" / t) for t in MAIN_TESTS if (MAIN / "tests" / t).exists()]
    proc = subprocess.run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", "--continue-on-collection-errors",
                           "--basetemp", str(W / "tmp-main"),
                           *tests], cwd=staged, env={"PYTHONPATH": str(staged), "PATH": "/usr/bin:/bin",
                                                     "HOME": str(Path.home())}, capture_output=True, text=True, timeout=3000)
    out["main_tests_on_staged"] = {"files": [Path(t).name for t in tests], "tail": proc.stdout.strip().splitlines()[-1:],
                                   "bad": sorted(set(re.findall(r"^(?:FAILED|ERROR) (\S+)", proc.stdout, re.M)))}
    failures = {}
    for p in sorted((staged / "eval").glob("*.py")):
        r = subprocess.run([PY, "-c", f"import importlib; importlib.import_module('eval.{p.stem}')"], cwd=staged,
                           env={"PYTHONPATH": str(staged), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            failures[p.stem] = r.stderr.strip().splitlines()[-1:]
    base_fail = {}
    for name in failures:
        r = subprocess.run([PY, "-c", f"import importlib; importlib.import_module('eval.{name}')"], cwd=base,
                           env={"PYTHONPATH": str(base), "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
                           capture_output=True, text=True, timeout=120)
        if r.returncode:
            base_fail[name] = True
    out["eval_import_failures_new_in_staged"] = {k: v for k, v in failures.items() if k not in base_fail}
    out["eval_import_failures_preexisting"] = sorted(base_fail)
    (HERE / "stage-test.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "overlaid"}, indent=1)[:4000])


if __name__ == "__main__":
    main()
