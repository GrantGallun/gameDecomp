"""Promote the fully verified WSL-native snapshot with recoverable old files."""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path

CONTROL = Path("/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908")
TARGET = Path("/home/grant/decomp/runs/resume-pipeline-20260908")
STAGED = TARGET / ".refresh-20260920-wsl-state-relocation"
SUFFIX = ".pre-20260920-wsl-state-relocation"
EXPECTED = {
    "campaign.sqlite": "d60225ee1e2d2f95418115f0b977024a8a32a3a788f324254eb8b29c5fc06836",
    "campaign.state.sqlite": "01091e3bddb71098b587d957e5c2eec710cd85f66fc51dfee7fbafadf748054b",
    "campaign.json": "ddffcadac8499b89b96cc137582d8a63b59a53d071d2713ed952b3885115dbeb",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def lock(path: Path):
    handle = path.open("a+b")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def main() -> None:
    if TARGET.resolve() != Path("/home/grant/decomp/runs/resume-pipeline-20260908"):
        raise RuntimeError("refusing unexpected target")
    for name, expected in EXPECTED.items():
        if sha256(STAGED / name) != expected:
            raise RuntimeError(f"staged hash differs: {name}")
    names = ("campaign.sqlite", "campaign.state.sqlite", "binary-data", "campaign.json")
    for name in names:
        if not (TARGET / name).exists() or not (STAGED / name).exists():
            raise RuntimeError(f"missing live or staged path: {name}")
        if (TARGET / (name + SUFFIX)).exists():
            raise RuntimeError(f"backup already exists: {name + SUFFIX}")

    locks = [lock(CONTROL / "resume-supervisor.lock"), lock(TARGET / "campaign.lock")]
    backed_up: list[str] = []
    promoted: list[str] = []
    try:
        for name in names:
            os.replace(TARGET / name, TARGET / (name + SUFFIX))
            backed_up.append(name)
        for name in names:
            os.replace(STAGED / name, TARGET / name)
            promoted.append(name)
    except Exception:
        for name in reversed(promoted):
            os.replace(TARGET / name, STAGED / name)
        for name in reversed(backed_up):
            os.replace(TARGET / (name + SUFFIX), TARGET / name)
        raise
    finally:
        for handle in reversed(locks):
            handle.close()

    print({
        "status": "promoted",
        "target": str(TARGET),
        "backups": [str(TARGET / (name + SUFFIX)) for name in names],
    })


if __name__ == "__main__":
    main()
