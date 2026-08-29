"""Experiment fingerprints: refuse to mix results from different implementations.

Review finding 4. Resume keyed only on function name, and the output filename
encoded the model and two flags -- not sample count, temperature, prompt
version, solver revision or seed. Results from different code could accumulate
in one file and be compared as if they came from one system.

That is not hypothetical here. Over one evening the solver changed underneath
the eval repeatedly: sampling budget went 4 -> 3, three prompt hints were wired
in (after silently not being wired in), routing changed from score bands to
workbench verdicts, and num_ctx became adaptive. Any of those resuming into an
older results file would have produced a number describing no system that ever
existed.

The fingerprint covers everything that can change an outcome:

    git revision + dirty flag   what code ran
    eval set content hash        which functions, which split
    model + sampling params      how the model was driven
    solver source hashes         catches edits made without committing

A mismatch is refused, not warned about. A warning gets ignored at 2am.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Source whose content can change a result. Hashed so an uncommitted edit is
# still caught -- the git revision alone would miss a working-tree change.
TRACKED_SOURCE = [
    "solver/pipeline.py", "solver/refine.py", "solver/llm.py",
    "solver/diagnose.py", "solver/context.py", "solver/siblings.py",
    "solver/workspace.py", "patterns/catalog.py",
]


@dataclass
class Fingerprint:
    git_rev: str
    git_dirty: bool
    set_hash: str
    split: str
    model: str
    samples: int
    temperature: float
    think: str
    pipeline: bool
    siblings: bool
    permute_seconds: int
    source_hash: str

    def digest(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()[:16]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def _source_hash() -> str:
    h = hashlib.sha1()
    for rel in sorted(TRACKED_SOURCE):
        p = ROOT / rel
        h.update(rel.encode())
        h.update(p.read_bytes() if p.exists() else b"<missing>")
    return h.hexdigest()[:16]


def build(set_path: Path, split: str, model: str, samples: int,
          temperature: float, think: str, pipeline: bool, siblings: bool,
          permute_seconds: int) -> Fingerprint:
    return Fingerprint(
        git_rev=_git("rev-parse", "--short", "HEAD") or "no-git",
        git_dirty=bool(_git("status", "--porcelain")),
        set_hash=hashlib.sha1(set_path.read_bytes()).hexdigest()[:16],
        split=split,
        model=model,
        samples=samples,
        temperature=temperature,
        think=think,
        pipeline=pipeline,
        siblings=siblings,
        permute_seconds=permute_seconds,
        source_hash=_source_hash(),
    )


def sidecar(results_path: Path) -> Path:
    return results_path.with_suffix(results_path.suffix + ".fingerprint.json")


def check_or_claim(results_path: Path, fp: Fingerprint) -> tuple[bool, str]:
    """Return (may_resume, message).

    A fresh results file claims the fingerprint. An existing one must match, or
    resuming would blend two implementations into a single number.
    """
    side = sidecar(results_path)

    if not results_path.exists() or results_path.stat().st_size == 0:
        # a run that dies because its output directory does not exist has
    # burned nothing yet, but it wastes a launch and reads as a crash
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(asdict(fp), indent=2))
        return True, f"new run, fingerprint {fp.digest()}"

    if not side.exists():
        return False, (
            f"{results_path.name} has results but no fingerprint sidecar, so "
            f"the code that produced it is unknown. Move it aside and rerun.")

    old = Fingerprint(**json.loads(side.read_text()))
    if old.digest() == fp.digest():
        return True, f"resuming, fingerprint {fp.digest()} matches"

    diffs = [f"{k}: {getattr(old, k)!r} -> {getattr(fp, k)!r}"
             for k in asdict(fp) if getattr(old, k) != getattr(fp, k)]
    return False, (
        f"REFUSING TO RESUME -- configuration changed since these results were "
        f"written ({old.digest()} -> {fp.digest()}):\n  " + "\n  ".join(diffs) +
        f"\nResuming would mix implementations in one file. Move "
        f"{results_path.name} aside and start a fresh run.")


def record(conn, fp: Fingerprint, results_path: Path, status: str = "running"):
    """Persist the experiment so a result can be traced to its configuration."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY,
            digest TEXT NOT NULL,
            results_path TEXT NOT NULL,
            config TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at INTEGER NOT NULL,
            UNIQUE(digest, results_path)
        )""")
    conn.execute(
        "INSERT OR IGNORE INTO experiments (digest, results_path, config,"
        " status, started_at) VALUES (?,?,?,?,?)",
        (fp.digest(), str(results_path), json.dumps(asdict(fp)), status,
         int(time.time())))
    conn.commit()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", required=True, type=Path)
    args = ap.parse_args()
    fp = build(args.set, "dev", "gpt-oss:20b", 3, 0.7, "low", True, True, 90)
    print(json.dumps(asdict(fp), indent=2))
    print("digest:", fp.digest())
