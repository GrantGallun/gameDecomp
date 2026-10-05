"""Runtime amendment: the compile cache stores only the artifacts a build wrote, compressed.

Why: `code/eval/fast_runtime.py` globbed `<stem>*` after each successful build, so every compile-cache
entry snapshotted every earlier candidate's outputs left in the workspace, hex-encoded. On 2026-09-21
worker 2's cache reached 141 GB (one entry 25 MB, 1.5% of entries ever re-read); with the campaign DB
copies it filled C: and WSL stopped. User-approved 2026-09-22, following OPERATIONS.md: stopped worker,
archived checkpoint and old code, focused tests, before/after hashes, every unchanged pin verified.

Replay of this build's outputs is unchanged; old hex entries still replay. No candidate, receipt, model
setting, test input, budget, correctness gate, oracle, certificate or held-out set changes.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

CONTROL = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
STATE_RUN = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
STATE_PATH = STATE_RUN / "campaign.json"
PROJECT = CONTROL / "code"
REVISION = CONTROL / "revisions/20260922-compile-cache-artifacts"
TARGET = PROJECT / "eval/fast_runtime.py"
STAGED = REVISION / "staged/fast_runtime.py"
OLD_SHA256 = "59177d8810af"            # prefix; full value is read from the pin and archived
MODEL_DIGEST = "17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7"
INVENTORY_SHA256 = "07d058bf658106fc5f6d08d6836ea00a59a66083122c28d452cb8d8671eabbdb"
PYTHON = "/home/grant/decomp/sbk1/.venv/bin/python"
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
    for label, args in (("frozen-suite", [str(PROJECT / "tests/test_campaign_fast.py"), str(PROJECT / "tests/test_fresh_compile.py")]),
                        ("amendment", [str(REVISION / "test_amendment.py")])):
        proc = subprocess.run([str(PYTEST_VENV), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "--basetemp", str(tmp / label), *args], cwd=PROJECT, env=env,
                              capture_output=True, text=True)
        runs[label] = {"returncode": proc.returncode, "tail": proc.stdout.strip().splitlines()[-1:]}
    return runs


def main() -> None:
    if not (CONTROL / "service.pause").exists():
        raise RuntimeError("campaign must be paused")
    locks = [unlocked(CONTROL / "resume-supervisor.lock"), unlocked(STATE_PATH.with_suffix(".lock"))]
    try:
        REVISION.mkdir(parents=True, exist_ok=True)
        pointer_before = STATE_PATH.read_bytes()
        state = campaign_state.read(STATE_PATH)
        key = str(TARGET)
        old_pin = state["pins"][key]
        if not old_pin.startswith(OLD_SHA256) or sha256(TARGET) != old_pin:
            raise RuntimeError("frozen fast_runtime is not the audited version")
        if state["model_digest"] != MODEL_DIGEST or state["inventory_sha256"] != INVENTORY_SHA256:
            raise RuntimeError("model or inventory pin changed")
        if state["config"]["project"] != str(PROJECT) or state.get("fast_inflight"):
            raise RuntimeError("unexpected project path or in-flight jobs")
        mismatches = [name for name, expected in state["pins"].items() if name != key
                      and (sha256(Path(name)) if Path(name).is_file() else None) != expected]
        if mismatches:
            raise RuntimeError(f"{len(mismatches)} unchanged pins differ, e.g. {mismatches[:3]}")
        with sqlite3.connect(f"file:{STATE_RUN / 'campaign.sqlite'}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("campaign database quick_check failed")
            inventory = list(conn.execute("SELECT name,addr,size,insn_count FROM functions ORDER BY name"))
        if campaign.digest(inventory) != INVENTORY_SHA256:
            raise RuntimeError("campaign database inventory differs")

        # Archive before any write.
        (REVISION / "campaign.before.json").write_bytes(pointer_before)
        shutil.copy2(CONTROL / "launch.json", REVISION / "launch.before.json")
        (REVISION / "previous-code/eval").mkdir(parents=True, exist_ok=True)
        shutil.copy2(TARGET, REVISION / "previous-code/eval/fast_runtime.py")
        new_sha = sha256(STAGED)
        before = {"campaign_pointer_sha256": hashlib.sha256(pointer_before).hexdigest(),
                  "campaign_commit": json.loads(pointer_before)["commit"],
                  "launch_sha256": sha256(CONTROL / "launch.json"),
                  "fast_runtime_sha256": old_pin, "staged_sha256": new_sha,
                  "unchanged_pins_verified": len(state["pins"]) - 1, "database_quick_check": "ok"}
        campaign_state.atomic(REVISION / "before-hashes.json", before)

        shutil.copy2(STAGED, TARGET)
        try:
            if sha256(TARGET) != new_sha:
                raise RuntimeError("staged copy differs after install")
            scratch = Path.home() / "decomp/experiments/population-transfer-20260922/pt-amend"
            scratch.mkdir(parents=True, exist_ok=True)       # native FS: flock and sqlite need it
            tests = run_tests(scratch)
            if any(r["returncode"] != 0 for r in tests.values()):
                raise RuntimeError(f"focused tests failed: {tests}")
            record = {"kind": "compile-cache-built-artifacts-only", "applied_at": time.time(), "revision": str(REVISION),
                      "source_checkpoint": {"commit": before["campaign_commit"], "pointer_sha256": before["campaign_pointer_sha256"]},
                      "changed": {key: {"old_sha256": old_pin, "new_sha256": new_sha}},
                      "verified": {"unchanged_pins": before["unchanged_pins_verified"], "model_digest": MODEL_DIGEST,
                                   "inventory_sha256": INVENTORY_SHA256, "database_quick_check": "ok", "tests": tests},
                      "limits": ("Cache storage only: which files a successful build's cache entry holds, and their "
                                 "encoding. No candidate, receipt, model setting, test input, budget, correctness "
                                 "gate, oracle, certificate, or held-out set changed.")}
            state["pins"][key] = new_sha
            state.setdefault("runtime_amendments", []).append(record)
            campaign_state.Store(STATE_PATH).save(state)
        except BaseException:
            shutil.copy2(REVISION / "previous-code/eval/fast_runtime.py", TARGET)   # nothing re-pinned: restore
            raise
        restored = campaign_state.read(STATE_PATH)
        if restored["pins"] != state["pins"]:
            raise RuntimeError("checkpoint amendment round trip failed")
        launch_path = CONTROL / "launch.json"
        launch = json.loads(launch_path.read_bytes())
        launch_keys = [k for k in launch.get("code_hashes", {}) if k.endswith("/eval/fast_runtime.py")]
        for k in launch_keys:
            launch["code_hashes"][k] = new_sha
        if launch_keys:
            campaign_state.atomic(launch_path, launch)
        after_pointer = json.loads(STATE_PATH.read_bytes())
        record["result"] = {"commit": after_pointer["commit"], "manifest_sha256": after_pointer["sha256"],
                            "pointer_sha256": sha256(STATE_PATH), "launch_sha256": sha256(launch_path),
                            "launch_code_hash_keys_updated": launch_keys, "fast_runtime_sha256": sha256(TARGET),
                            "object_exact_or_integrated": after_pointer["summary"]["object_exact_or_integrated"]}
        campaign_state.atomic(REVISION / "amendment.json", record)
        campaign_state.atomic(REVISION / "after-hashes.json", record["result"])
        print(json.dumps(record, indent=2))
    finally:
        for handle in reversed(locks):
            handle.close()


if __name__ == "__main__":
    main()
