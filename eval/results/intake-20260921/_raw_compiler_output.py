"""The RAW compiler output for one state, because the stored verdict truncates it.

`solver.workspace.score` reads the build script's output and stores only the `ERROR_LINE_RE` matches in
`compiler_stderr`, plus the normalized score line. The caret/context lines that say WHAT is wrong are not
in that projection -- so diagnosing from it is diagnosing from a summary. This runs the same build the
oracle runs and prints the whole output.

Read-only: it compiles a candidate through the workspace's own build script and writes nothing back to the
KB.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import workspace                                            # noqa: E402

REPO = Path.home() / "decomp/sbk1"
NAMES = ("clearRaceReplayCourseGrid", "loadNextRaceReplayCourseGridEntry")

for name in NAMES:
    ws = workspace.bootstrap(REPO, name)
    candidate = workspace.m2c_draft(ws)
    if not candidate.strip():
        print(f"{name}: no draft")
        continue
    (ws / "probe_candidate.c").write_text(candidate, encoding="utf-8")
    script = ws / "build.sh"
    command = (f". {shlex.quote(str(REPO / '.venv/bin/activate'))} && "
               f"bash {shlex.quote(str(script))} probe_candidate.c")
    proc = subprocess.run(command, shell=True, cwd=ws, capture_output=True, text=True, timeout=300)
    print("=" * 78)
    print(f"{name}   exit={proc.returncode}   {len(candidate)} chars compiled")
    print("=" * 78)
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines()[:40]:
        print(f"  {line[:150]}")
    (ws / "probe_candidate.c").unlink(missing_ok=True)
    print()
