"""Runtime amendment: restore the 2026-09-13/14 scheduler profiles lost in amend-20260919-185725.

Why: amend-20260919-185725 overwrote the frozen tree with main-tree files. The census/regalloc wiring had been
written only into the frozen tree (20260913-regalloc-search, 20260914-census-address-recertify,
20260914-semantic-revalidate, 20260914-ninety-*, 20260914-frontend-fixits, 20260915-compile-chain), never into
main, so the overwrite removed it: the regalloc_search profile (184 of the run's 263 measured exact gains),
recertify@/revalidate@, address_symbols, named_rodata, stack_layout, structural_rewrites, placeholder_recovery,
draft_lowering, frontend_fixits, compile-chain and the zero-model dedup. Their tests stayed and have failed since.

Change: a three-way merge (base 20260913-regalloc-search/previous-code, ours = current frozen, theirs =
amend-20260919-185725/previous-code) of agentrepair.py and repair_queue.py, and a hand port of the four
completion_campaign.py hunks (the frozen file is CRLF, so the textual merge conflicted as a whole); the
compile_recovery placeholder/draft-lowering stage (cap 8 -> 12 as before) and the regalloc_search tie rule
("ties go to register allocation"), both found by a blob-provenance scan of the 09-19 archive against main's git;
and three tests brought back in line (revalidated() helper, regalloc_search exclusion, a fake's kwargs). Scheduling
and zero-model repair stages only. Operator instruction (2026-09-25): change machinery, never import matches.
Follows OPERATIONS.md: paused worker, archived checkpoint and old code, staged tests, before/after hashes,
every unchanged pin verified; any failure restores the previous files and changes no pin.
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
REVISION = CONTROL / "revisions/20260925-census-restore"
PREVIOUS = CONTROL / "revisions/20260925-solver-refresh/amendment.json"
PYTEST_VENV = Path.home() / "decomp/experiments/population-transfer-20260922/pytest-venv/bin/python"
FILES = ["eval/agentrepair.py", "eval/completion_campaign.py", "solver/repair_queue.py", "solver/compile_recovery.py",
         "solver/regalloc_search.py"]
TEST_FILES = ["tests/test_repair_queue.py", "tests/test_completion_campaign.py", "tests/test_enabling_roots_campaign.py"]

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
    env = dict(os.environ, PYTHONPATH=str(PROJECT))
    runs = {}
    for label, args in (("wiring", ["tests/test_census_campaign.py", "tests/test_ninety_campaign.py",
                                    "tests/test_repair_queue.py", "tests/test_regalloc_campaign.py",
                                    "tests/test_frontend_fixits_campaign.py", "tests/test_draft_lowering_campaign.py",
                                    "tests/test_compile_chain_campaign.py", "tests/test_enabling_roots_campaign.py",
                                    "tests/test_completion_campaign.py"]),
                        ("campaign", ["tests/test_campaign_fast.py", "tests/test_fresh_compile.py"])):
        proc = subprocess.run([str(PYTEST_VENV), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "--basetemp", str(tmp / label), *args], cwd=PROJECT, env=env,
                              capture_output=True, text=True)
        runs[label] = {"returncode": proc.returncode, "tail": proc.stdout.strip().splitlines()[-1:]}
    return runs


def main() -> None:
    stage = json.loads((REVISION / "stage-test.json").read_text())
    if stage["newly_failing_in_staged"] or stage["wiring_tests_staged"]["bad"]:
        raise RuntimeError("staged copy did not pass: see stage-test.json")
    previous = json.loads(PREVIOUS.read_text())
    if not (CONTROL / "service.pause").exists():
        raise RuntimeError("campaign must be paused")
    locks = [unlocked(CONTROL / "resume-supervisor.lock"), unlocked(STATE_PATH.with_suffix(".lock"))]
    try:
        pointer_before = STATE_PATH.read_bytes()
        state = campaign_state.read(STATE_PATH)
        files = {str(PROJECT / f): REVISION / "staged" / f for f in FILES}
        for target in files:
            if state["pins"].get(target) != sha256(Path(target)):
                raise RuntimeError(f"frozen {target} is not the pinned version")
        verified = previous["verified"]
        if state["model_digest"] != verified["model_digest"] or state["inventory_sha256"] != verified["inventory_sha256"]:
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
        if campaign.digest(inventory) != verified["inventory_sha256"]:
            raise RuntimeError("campaign database inventory differs")

        (REVISION / "campaign.before.json").write_bytes(pointer_before)
        shutil.copy2(CONTROL / "launch.json", REVISION / "launch.before.json")
        for f in FILES + TEST_FILES:
            (REVISION / "previous-code" / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT / f, REVISION / "previous-code" / f)
        staged_sha = {t: sha256(s) for t, s in files.items()}
        before = {"campaign_pointer_sha256": hashlib.sha256(pointer_before).hexdigest(),
                  "campaign_commit": json.loads(pointer_before)["commit"],
                  "launch_sha256": sha256(CONTROL / "launch.json"),
                  "old_pins": {t: state["pins"].get(t) for t in files}, "staged_sha256": staged_sha,
                  "unchanged_pins_verified": len([n for n in state["pins"] if n not in files]),
                  "database_quick_check": "ok"}
        campaign_state.atomic(REVISION / "before-hashes.json", before)

        def restore():
            for f in FILES + TEST_FILES:
                shutil.copy2(REVISION / "previous-code" / f, PROJECT / f)

        for target, source in files.items():
            shutil.copy2(source, target)
        for f in TEST_FILES:                               # unpinned: the tests that describe the restored wiring
            shutil.copy2(REVISION / "staged" / f, PROJECT / f)
        try:
            if any(sha256(Path(t)) != staged_sha[t] for t in files):
                raise RuntimeError("staged copy differs after install")
            scratch = Path.home() / "decomp/experiments/census-restore-20260925/amend"
            scratch.mkdir(parents=True, exist_ok=True)
            tests = run_tests(scratch)
            if any(r["returncode"] != 0 for r in tests.values()):
                raise RuntimeError(f"focused tests failed: {tests}")
            record = {"kind": "scheduler-profile-restore", "applied_at": time.time(), "revision": str(REVISION),
                      "source_checkpoint": {"commit": before["campaign_commit"],
                                            "pointer_sha256": before["campaign_pointer_sha256"]},
                      "restores": ["20260913-regalloc-search", "20260914-census-address-recertify",
                                   "20260914-semantic-revalidate", "20260914-ninety-*", "20260914-frontend-fixits",
                                   "20260915-compile-chain"],
                      "lost_in": "amend-20260919-185725",
                      "changed": {t: {"old_sha256": before["old_pins"][t], "new_sha256": staged_sha[t]} for t in files},
                      "tests_changed": TEST_FILES,
                      "verified": {"unchanged_pins": before["unchanged_pins_verified"],
                                   "model_digest": verified["model_digest"],
                                   "inventory_sha256": verified["inventory_sha256"], "database_quick_check": "ok",
                                   "tests": tests, "staged_copy": "stage-test.json"},
                      "limits": ("Scheduling and zero-model repair stages only, restored as previously authorized "
                                 "and measured. No candidate, receipt, model setting, test input, model budget, "
                                 "held-out set, certificate, checkpoint result or ledger row changed; no external "
                                 "match was imported. recertify@/revalidate@ digests are computed from the current "
                                 "certificate and semantic code, so each eligible node is re-scored once.")}
            for t in files:
                state["pins"][t] = staged_sha[t]
            state.setdefault("runtime_amendments", []).append(record)
            campaign_state.Store(STATE_PATH).save(state)
        except BaseException:
            restore()
            raise
        if campaign_state.read(STATE_PATH)["pins"] != state["pins"]:
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
