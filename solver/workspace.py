"""Per-function matching workspace: the Oracle for a single function.

Wraps the host repo's own tooling (`tools/claude --bootstrap-only` and
`build.sh`) rather than reimplementing it. That tooling is mature, already
enforces the project's rules, and reports a verified exact-match verdict --
there is nothing to gain from a second implementation that could disagree
with it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCORE_RE = re.compile(r"^Score:\s*([\d.]+)%", re.MULTILINE)
EXACT_RE = re.compile(r"^Verified exact match:\s*(\w+)", re.MULTILINE)
ERROR_LINE_RE = re.compile(r"^(?:cfe: Error|ERROR|.*Syntax Error).*$", re.MULTILINE)


@dataclass
class Attempt:
    compiled: bool
    score: float          # 0..100; 100 means byte-exact
    exact: bool
    diff: str             # instruction diff, empty when it did not compile
    compiler_stderr: str
    raw_output: str


def sh(cmd: str, cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    proc = subprocess.run(["bash", "-lc", cmd], cwd=cwd, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def bootstrap(repo: Path, func: str) -> Path:
    ws = repo / "nonmatchings" / func
    if not (ws / "target.s").exists():
        _, out = sh(f". .venv/bin/activate && ./tools/claude --bootstrap-only {func}",
                    cwd=repo, timeout=900)
        if not (ws / "target.s").exists():
            raise RuntimeError(f"bootstrap failed for {func}:\n{out[-800:]}")
    return ws


def target_asm(ws: Path, func: str) -> str:
    """Just the function body; target.s carries a macro preamble first."""
    text = (ws / "target.s").read_text(errors="replace")
    start = text.find(f"glabel {func}")
    if start == -1:
        raise RuntimeError(f"glabel {func} not found in target.s")
    end = text.find(f"endlabel {func}")
    return text[start:end if end != -1 else None].strip()


def m2c_draft(ws: Path) -> str:
    path = ws / "base.c"
    return path.read_text(errors="replace") if path.exists() else ""


def assert_uncontaminated(prompt: str, repo: Path, func: str) -> None:
    """Fail loudly if ground-truth source leaked into the prompt.

    The workspace's generated ctx.c contains the preprocessed translation unit
    INCLUDING the target function's own body. On an already-matched function
    that is the answer, and using it would fabricate a 100% that silently
    invalidates every number afterwards. Checked at runtime, not asserted in a
    comment.
    """
    _, out = sh(f"grep -rn --include=*.c -A 8 '^[a-zA-Z_].*{func}(' src/ | head -20",
                cwd=repo)
    for line in out.splitlines():
        body = line.split(":", 2)[-1].strip()
        if len(body) > 25 and body.endswith(";") and body in prompt:
            raise RuntimeError(
                f"CONTAMINATION: ground-truth line leaked into prompt for {func}:\n  {body}")


def log_attempt(conn, func: str, code: str, att: "Attempt", *,
                strategy: str = "adhoc", model: str = "", prompt: str = "",
                temperature=None, wall_ms: int = 0, run_id: str = "",
                extra: dict | None = None) -> bool:
    """Record one attempt. Returns True if a row was written.

    CLAUDE.md requires every attempt to be logged, including failures -- it is
    the debugging record now and the training set later. Only refine.py ever
    did, so every ad-hoc experiment harness wrote ZERO rows: an entire day of
    runs, ~250 generations, left no trace and cannot be recovered.

    Living next to `score` so the two are hard to separate, and tolerant of
    missing metadata so a harness has no excuse not to call it. A row with
    strategy='adhoc' and no timing is still worth vastly more than no row.
    """
    import json
    import time as _time
    if conn is None:
        return False
    row = conn.execute("select addr from functions where name=?",
                       (func,)).fetchone()
    if not row:
        return False
    sampling = {"temperature": temperature, "run_id": run_id}
    if extra:
        sampling.update(extra)
    conn.execute(
        "INSERT INTO attempts (func_addr, iteration, source_code,"
        " prompt_context, compiled, compiler_stderr, score, diff_summary,"
        " strategy, model, sampling, wall_ms, token_cost, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row[0], 0, code, prompt, int(att.compiled), att.compiler_stderr,
         att.score, att.diff, strategy, model, json.dumps(sampling),
         wall_ms, 0, int(_time.time())))
    conn.commit()
    return True


def score(ws: Path, repo: Path, name: str, code: str, conn=None,
          func: str = "", **log_kw) -> Attempt:
    """Compile one candidate and score it against the target object.

    Pass `conn` and `func` to log the attempt automatically. Optional so no
    existing caller breaks, but every new harness should pass them -- see
    log_attempt for why.
    """
    (ws / f"{name}.c").write_text(code)
    _, out = sh(f". {repo}/.venv/bin/activate && ./build.sh {name}.c", cwd=ws, timeout=300)

    m = SCORE_RE.search(out)
    if not m:
        errors = "\n".join(ERROR_LINE_RE.findall(out)[:6])
        att = Attempt(False, 0.0, False, "", errors or out[-700:], out)
    else:
        exact_m = EXACT_RE.search(out)
        exact = bool(exact_m and exact_m.group(1) == "yes")
        diff_path = ws / f"{name}_diff"
        diff = diff_path.read_text(errors="replace") if diff_path.exists() else ""
        att = Attempt(True, float(m.group(1)), exact, diff, "", out)

    # Failures are logged too: the non-compiling rows are exactly what made
    # today's extraction bugs findable.
    if conn is not None and func:
        log_attempt(conn, func, code, att, **log_kw)
    return att
