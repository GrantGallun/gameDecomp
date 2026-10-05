"""Apply the recorded split-state relocation after database copies validate.

This is a one-run maintenance receipt, not campaign machinery.  It refuses any
checkpoint, pin, inventory, model, or command shape other than the one audited
for resume-pipeline-20260908.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

CONTROL = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
STATE_RUN = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
REVISION = CONTROL / "revisions/20260920-wsl-state-relocation"
PROJECT = CONTROL / "code"
STATE_PATH = STATE_RUN / "campaign.json"
SERVICE_CODE = PROJECT / "eval/campaign_service.py"
OLD_SERVICE_SHA256 = "4902173eb3184b2207614b30b97fe32def888915c2f8c1e54756398109957fde"
NEW_SERVICE_SHA256 = "5c7257268dc4b4ab59d857d1b5e6d8d5ac7af1762ec2e3bbd10ff2e0b8cb5c17"
SOURCE_POINTER_SHA256 = "ddffcadac8499b89b96cc137582d8a63b59a53d071d2713ed952b3885115dbeb"
MODEL_DIGEST = "17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7"
INVENTORY_SHA256 = "07d058bf658106fc5f6d08d6836ea00a59a66083122c28d452cb8d8671eabbdb"

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


def command_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def main() -> None:
    locks = [unlocked(CONTROL / "resume-supervisor.lock"),
             unlocked(STATE_PATH.with_suffix(".lock"))]
    try:
        source_pointer = (REVISION / "campaign.source.before.json").read_bytes()
        if sha256(REVISION / "campaign.source.before.json") != SOURCE_POINTER_SHA256:
            raise RuntimeError("archived source pointer changed")
        pointer = json.loads(STATE_PATH.read_bytes())
        source = json.loads(source_pointer)
        if any(pointer[k] != source[k] for k in ("kind", "store", "commit", "sha256")):
            raise RuntimeError("native checkpoint is not the archived stopped source checkpoint")

        state = campaign_state.read(STATE_PATH)
        if state["model_digest"] != MODEL_DIGEST or state["inventory_sha256"] != INVENTORY_SHA256:
            raise RuntimeError("model or inventory pin changed")
        if state["config"]["project"] != str(PROJECT):
            raise RuntimeError("unexpected frozen project path")
        if state["config"]["db"] != str(CONTROL / "campaign.sqlite"):
            raise RuntimeError("unexpected source database path")
        if len(state.get("fast_inflight", [])) != 3:
            raise RuntimeError("interrupted job set changed")
        if state["pins"].get(str(SERVICE_CODE)) != OLD_SERVICE_SHA256:
            raise RuntimeError("recorded supervisor pin is not the archived version")
        if sha256(SERVICE_CODE) != NEW_SERVICE_SHA256:
            raise RuntimeError("staged supervisor code hash differs")

        mismatches = []
        for name, expected in state["pins"].items():
            if name == str(SERVICE_CODE):
                continue
            path = Path(name)
            actual = sha256(path) if path.is_file() else None
            if actual != expected:
                mismatches.append({"path": name, "expected": expected, "actual": actual})
                if len(mismatches) == 5:
                    break
        if mismatches:
            raise RuntimeError("unchanged frozen pins differ: " + json.dumps(mismatches))

        with sqlite3.connect(STATE_RUN / "campaign.sqlite") as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("campaign database quick_check failed")
            inventory = list(conn.execute(
                "SELECT name,addr,size,insn_count FROM functions ORDER BY name"))
        if campaign.digest(inventory) != INVENTORY_SHA256:
            raise RuntimeError("campaign database inventory differs")

        binary = STATE_RUN / state["binary_data_catalog"]["path"]
        if sha256(binary) != state["binary_data_catalog"]["sha256"]:
            raise RuntimeError("binary-data cache object differs")

        record = {
            "kind": "wsl-state-relocation-and-supervisor-path-fix",
            "applied_at": time.time(),
            "revision": str(REVISION),
            "source_checkpoint": {
                "commit": source["commit"],
                "manifest_sha256": source["sha256"],
                "pointer_sha256": SOURCE_POINTER_SHA256,
            },
            "changed": {
                "config.db": {
                    "old": state["config"]["db"],
                    "new": str(STATE_RUN / "campaign.sqlite"),
                },
                str(SERVICE_CODE): {
                    "old_sha256": OLD_SERVICE_SHA256,
                    "new_sha256": NEW_SERVICE_SHA256,
                },
                "launch.--state": str(STATE_PATH),
                "launch.--project": str(PROJECT),
                "launch.--db": str(STATE_RUN / "campaign.sqlite"),
            },
            "verified": {
                "unchanged_pins": len(state["pins"]) - 1,
                "model_digest": MODEL_DIGEST,
                "inventory_sha256": INVENTORY_SHA256,
                "binary_data_sha256": state["binary_data_catalog"]["sha256"],
                "inflight_receipts": len(state["fast_inflight"]),
                "database_quick_check": "ok",
            },
            "limits": (
                "Paths and supervisor control only. No candidate, receipt, model setting, "
                "test input, budget, correctness gate, oracle, certificate, or held-out set changed."
            ),
        }
        state["config"]["db"] = str(STATE_RUN / "campaign.sqlite")
        state["pins"][str(SERVICE_CODE)] = NEW_SERVICE_SHA256
        state.setdefault("runtime_amendments", []).append(record)
        campaign_state.Store(STATE_PATH).save(state)

        restored = campaign_state.read(STATE_PATH)
        if restored["pins"] != state["pins"] or restored["config"] != state["config"]:
            raise RuntimeError("checkpoint amendment round trip failed")

        launch_path = CONTROL / "launch.json"
        launch = json.loads(launch_path.read_bytes())
        command = launch["command"]
        command[command.index("--state") + 1] = str(STATE_PATH)
        command[command.index("--project") + 1] = str(PROJECT)
        command[command.index("--db") + 1] = str(STATE_RUN / "campaign.sqlite")
        if command_value(command, "--model") != restored["config"]["model"]:
            raise RuntimeError("launch model changed")
        if command_value(command, "--endpoint") != restored["config"]["endpoint"]:
            raise RuntimeError("launch endpoint changed")
        launch["code_hashes"][str(SERVICE_CODE)] = NEW_SERVICE_SHA256
        campaign_state.atomic(launch_path, launch)

        after_pointer = json.loads(STATE_PATH.read_bytes())
        record["result"] = {
            "commit": after_pointer["commit"],
            "manifest_sha256": after_pointer["sha256"],
            "pointer_sha256": sha256(STATE_PATH),
            "launch_sha256": sha256(launch_path),
            "service_code_sha256": sha256(SERVICE_CODE),
            "object_exact_or_integrated": after_pointer["summary"]["object_exact_or_integrated"],
        }
        campaign_state.atomic(REVISION / "amendment.json", record)
        print(json.dumps(record, indent=2))
    finally:
        for handle in reversed(locks):
            handle.close()


if __name__ == "__main__":
    main()
