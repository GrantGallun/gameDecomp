"""Runtime amendment: refresh the campaign's solver machinery to the main tree (21 files; closure.json).

Why: the campaign runs solver code frozen on 2026-09-17. The main tree's machinery since then (evidence passed to the
mutation stream, evidence_site, index_form, branch_shape families incl. o1_register_saved, owner rewrites, and the
certificate's pairing and rodata-value stages) closed 32 campaign-stuck functions on 2026-09-25 when run outside
the campaign (eval/results/operand-repair-20260925). Operator instruction (2026-09-25): change the MACHINERY only and
let the campaign reach those functions itself; the campaign's checkpoint results and ledger are not edited or
imported. Follows OPERATIONS.md: stopped worker, archived checkpoint and old code, focused tests, before/after hashes,
every unchanged pin verified; any failure restores the previous files and changes no pin.

Changed: solver files only (9 replaced, 12 added). No candidate, receipt, model setting, test input, budget,
held-out set, checkpoint result or ledger row changes. The certificate itself changes (two recorded second stages);
this is disclosed in the amendment record.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CONTROL = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
STATE_RUN = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
STATE_PATH = STATE_RUN / "campaign.json"
PROJECT = CONTROL / "code"
MAIN = Path("/mnt/c/Code/gameDecomp")
REVISION = CONTROL / "revisions/20260925-solver-refresh"
PREVIOUS = CONTROL / "revisions/20260922-compile-cache-artifacts/amendment.json"
PYTEST_VENV = Path.home() / "decomp/experiments/population-transfer-20260922/pytest-venv/bin/python"

sys.path.insert(0, str(PROJECT))
from eval import campaign_state  # noqa: E402
from eval import completion_campaign as campaign  # noqa: E402


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unlocked(path: Path):
    handle = path.open("a+b")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f"lock held: {path}") from exc
    return handle


def run_tests(tmp: Path) -> dict:
    env = dict(os.environ, AMENDMENT_PROJECT=str(PROJECT), PYTHONPATH=str(PROJECT))
    runs = {}
    for label, args in (("frozen-suite", [str(PROJECT / "tests/test_campaign_fast.py"),
                                          str(PROJECT / "tests/test_fresh_compile.py")]),
                        ("amendment", [str(REVISION / "test_amendment.py")])):
        proc = subprocess.run([str(PYTEST_VENV), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "--basetemp", str(tmp / label), *args], cwd=PROJECT, env=env,
                              capture_output=True, text=True)
        runs[label] = {"returncode": proc.returncode, "tail": proc.stdout.strip().splitlines()[-1:]}
    return runs


def main() -> None:
    closure = json.loads((REVISION / "closure.json").read_text())
    stage = json.loads((REVISION / "stage-test.json").read_text())
    if stage["newly_failing_in_staged"] or stage["eval_import_failures_new_in_staged"] or stage["main_tests_on_staged"]["bad"]:
        raise RuntimeError("staged copy did not pass: see stage-test.json")
    previous = json.loads(PREVIOUS.read_text())["verified"]
    if not (CONTROL / "service.pause").exists():
        raise RuntimeError("campaign must be paused")
    locks = [unlocked(CONTROL / "resume-supervisor.lock"), unlocked(STATE_PATH.with_suffix(".lock"))]
    try:
        pointer_before = STATE_PATH.read_bytes()
        state = campaign_state.read(STATE_PATH)
        files = {str(PROJECT / "solver" / f"{m}.py"): MAIN / "solver" / f"{m}.py" for m in closure["changed"]}
        for target in files:
            pin = state["pins"].get(target)
            if pin is not None and sha256(Path(target)) != pin:
                raise RuntimeError(f"frozen {target} is not the pinned version")
        if state["model_digest"] != previous["model_digest"] or state["inventory_sha256"] != previous["inventory_sha256"]:
            raise RuntimeError("model or inventory pin changed since the previous amendment")
        if state["config"]["project"] != str(PROJECT) or state.get("fast_inflight"):
            raise RuntimeError("unexpected project path or in-flight jobs")
        mismatches = [name for name, expected in state["pins"].items() if name not in files
                      and (sha256(Path(name)) if Path(name).is_file() else None) != expected]
        if mismatches:
            raise RuntimeError(f"{len(mismatches)} unchanged pins differ, e.g. {mismatches[:3]}")
        with sqlite3.connect(f"file:{STATE_RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("campaign database quick_check failed")
            inventory = list(conn.execute("SELECT name,addr,size,insn_count FROM functions ORDER BY name"))
        if campaign.digest(inventory) != previous["inventory_sha256"]:
            raise RuntimeError("campaign database inventory differs")

        # Archive before any write.
        (REVISION / "campaign.before.json").write_bytes(pointer_before)
        shutil.copy2(CONTROL / "launch.json", REVISION / "launch.before.json")
        (REVISION / "previous-code/solver").mkdir(parents=True, exist_ok=True)
        (REVISION / "staged/solver").mkdir(parents=True, exist_ok=True)
        existed = {t: Path(t).exists() for t in files}
        for target, source in files.items():
            if existed[target]:
                shutil.copy2(target, REVISION / "previous-code/solver" / Path(target).name)
            shutil.copy2(source, REVISION / "staged/solver" / Path(target).name)
        staged_sha = {t: sha256(REVISION / "staged/solver" / Path(t).name) for t in files}
        before = {"campaign_pointer_sha256": hashlib.sha256(pointer_before).hexdigest(),
                  "campaign_commit": json.loads(pointer_before)["commit"],
                  "launch_sha256": sha256(CONTROL / "launch.json"),
                  "old_pins": {t: state["pins"].get(t) for t in files}, "staged_sha256": staged_sha,
                  "unchanged_pins_verified": len([n for n in state["pins"] if n not in files]),
                  "database_quick_check": "ok"}
        campaign_state.atomic(REVISION / "before-hashes.json", before)

        def restore():
            for target in files:
                if existed[target]:
                    shutil.copy2(REVISION / "previous-code/solver" / Path(target).name, target)
                elif Path(target).exists():
                    Path(target).unlink()

        for target in files:
            shutil.copy2(REVISION / "staged/solver" / Path(target).name, target)
        try:
            if any(sha256(Path(t)) != staged_sha[t] for t in files):
                raise RuntimeError("staged copy differs after install")
            scratch = Path.home() / "decomp/experiments/solver-refresh-20260925/amend"
            scratch.mkdir(parents=True, exist_ok=True)
            tests = run_tests(scratch)
            if any(r["returncode"] != 0 for r in tests.values()):
                raise RuntimeError(f"focused tests failed: {tests}")
            record = {"kind": "solver-machinery-refresh", "applied_at": time.time(), "revision": str(REVISION),
                      "source_checkpoint": {"commit": before["campaign_commit"],
                                            "pointer_sha256": before["campaign_pointer_sha256"]},
                      "changed": {t: {"old_sha256": before["old_pins"][t], "new_sha256": staged_sha[t],
                                      "added": not existed[t]} for t in files},
                      "verified": {"unchanged_pins": before["unchanged_pins_verified"],
                                   "model_digest": previous["model_digest"],
                                   "inventory_sha256": previous["inventory_sha256"], "database_quick_check": "ok",
                                   "tests": tests, "staged_copy": "stage-test.json"},
                      "limits": ("Solver machinery only: mutation families, evidence passed to them, and two recorded "
                                 "certificate stages (same-addend HI16/LO16 pairing; rodata values). No candidate, "
                                 "receipt, model setting, test input, budget, held-out set, checkpoint result or "
                                 "ledger row changed; no external match was imported.")}
            for t in files:
                state["pins"][t] = staged_sha[t]
            state.setdefault("runtime_amendments", []).append(record)
            campaign_state.Store(STATE_PATH).save(state)
        except BaseException:
            restore()
            raise
        restored = campaign_state.read(STATE_PATH)
        if restored["pins"] != state["pins"]:
            raise RuntimeError("checkpoint amendment round trip failed")
        launch_path = CONTROL / "launch.json"
        launch = json.loads(launch_path.read_bytes())
        keys = [k for k in launch.get("code_hashes", {}) if k in files]
        for k in keys:
            launch["code_hashes"][k] = staged_sha[k]
        if keys:
            campaign_state.atomic(launch_path, launch)
        after = json.loads(STATE_PATH.read_bytes())
        record["result"] = {"commit": after["commit"], "manifest_sha256": after["sha256"],
                            "pointer_sha256": sha256(STATE_PATH), "launch_sha256": sha256(launch_path),
                            "launch_code_hash_keys_updated": keys,
                            "object_exact_or_integrated": after["summary"]["object_exact_or_integrated"]}
        campaign_state.atomic(REVISION / "amendment.json", record)
        campaign_state.atomic(REVISION / "after-hashes.json", record["result"])
        print(json.dumps(record["result"], indent=2))
    finally:
        for handle in reversed(locks):
            handle.close()


if __name__ == "__main__":
    main()
