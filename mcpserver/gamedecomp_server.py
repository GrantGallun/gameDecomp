"""An MCP server that lets a model test its own decompilation attempts against the binary.

Why this shape. Everything upstream of this file assumes the model proposes and something else
decides. Today the deciding happens in a campaign loop the model cannot call, so a model's only way
to learn what the compiler thinks of its C is a human or a batch job coming back later. That makes
one-shot generation the only cheap strategy, and one-shot generation is the ~1.2% road this project
already priced as dead.

The measured bottleneck is the same thing from the other side. The knowledge base holds 5,952 edges
where both ends compiled, but only **357 improving** ones, and only **114 parents** have both an
improving and a regressing child -- 251 preference pairs over 19 functions. That is not enough to
teach anything. Improvement trajectories are the scarce good, and the way to manufacture them is to
let the model iterate against the compiler itself, thousands of times, unattended.

So the tools here are the self-test surface:

    list_functions        where to work, ranked by tractability, not by score
    function_status       the target assembly and the current residual, by kind
    score_candidate       compile one candidate and return score, exactness and fault classes
    classify_residual     what KIND of wrong a diff is, which is what decides the next move
    evidence_for          the binary-derived facts for this function (never a guessed field)
    practice_function     a verified (assembly, C) pair from the generated corpus
    trajectory_status     how much improvement data exists, so the model can see the scarcity

`score_candidate` is the one that matters: it is the compiler, wrapped, with no model in the loop.
A model that can call it can hill-climb instead of guessing, and every call is an attempt the project
logs.

The tool implementations are plain functions so they can be tested without the MCP transport; the
server wiring is a thin shell on top.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import signals, workspace  # noqa: E402

AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


@dataclass
class Context:
    """Where to work. Defaults are this machine's, and every tool takes them as arguments instead
    of reading globals, so a test can point at a fixture."""
    repo: Path = Path.home() / "decomp" / "sbk1"
    kb: Path = Path.home() / "decomp" / "kb-sbk1.sqlite"
    pairs: Path = ROOT / "eval" / "results" / "corpus-grabber-20260916" / "dkr-pal-v80-receipt.json"

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.kb}?mode=ro", uri=True)

    def connect_write(self) -> sqlite3.Connection:
        """A WRITABLE handle, because a scored candidate is an attempt and attempts are logged.

        `connect()` is read-only and always was. That made `score_candidate(log=True)` impossible to
        honour: the write would have raised. Every tool here reads through `connect()`; only the one
        that compiles opens this. `timeout` matters because the campaign also writes this file.
        """
        conn = sqlite3.connect(str(self.kb), timeout=120)
        conn.row_factory = sqlite3.Row
        return conn


def _profile(diff: str, score: float = 0.0, exact: bool = False,
             compiled: bool = True) -> dict[str, int]:
    verdict = signals.analyse(diff or "", score, exact, compiled)
    return {axis: int(getattr(verdict, axis)) for axis in AXES}


# The `functions` table in this knowledge base is the COMPLETED decomp's inventory: every row says
# `state='matched'` with `best_score` NULL and `attempts` 0, because it records what the game is, not
# what the solver has done. Reading progress from it returns nothing, which is exactly what happened
# the first time this tool was pointed at the real knowledge base. Everything about progress is
# therefore derived from the `attempts` table.
CANDIDATE_SQL = """
select f.addr, f.name, f.insn_count, f.is_leaf,
       (select max(a.score) from attempts a where a.func_addr = f.addr and a.compiled = 1),
       (select count(*) from attempts a where a.func_addr = f.addr),
       (select max(coalesce(a.exact, 0)) from attempts a where a.func_addr = f.addr),
       (select a.diff_summary from attempts a where a.func_addr = f.addr and a.compiled = 1
        order by a.score desc limit 1),
       (select a.id from attempts a where a.func_addr = f.addr and a.compiled = 1
        order by a.score desc limit 1)
from functions f
where exists (select 1 from attempts a where a.func_addr = f.addr and a.compiled = 1)
"""


def list_functions(ctx: Context, limit: int = 20, min_attempts: int = 1,
                   skip_solved: bool = True) -> list[dict]:
    """Candidate functions, ranked by how much of the residual an implemented pass actually owns.

    Ranked by tractability rather than by score on purpose: a function at 70% whose residual is two
    offset faults is closer to done than one at 96% whose residual is thirty branch faults, and a
    model given a score-ordered list will spend its whole budget on the second kind.
    """
    conn = ctx.connect()
    out = []
    for addr, name, insns, leaf, best, attempts, solved, diff, best_id in conn.execute(
            CANDIDATE_SQL).fetchall():
        if diff is None or attempts < min_attempts or (skip_solved and solved):
            continue
        verdict = signals.analyse(diff, best or 0.0, False, True)
        owned = verdict.repairable + verdict.conditional_repair
        total = owned + verdict.no_repair_implemented + verdict.unrepairable
        out.append({"addr": addr, "name": name, "insns": insns, "leaf": bool(leaf),
                    "best_score": best, "attempts": attempts, "best_attempt_id": best_id,
                    "solved": bool(solved), "faults": _profile(diff, best or 0.0),
                    "owned_share": round(owned / total, 3) if total else 0.0,
                    "tractable_faults": owned})
    out.sort(key=lambda row: (-(row["owned_share"] * 100), row["tractable_faults"],
                              -(row["best_score"] or 0.0)))
    return out[:limit]


def function_status(ctx: Context, addr: int) -> dict:
    """Everything known about one function: the target assembly, the best attempt, and why it fails."""
    conn = ctx.connect()
    row = conn.execute("select name, insn_count, is_leaf, tu_id from functions where addr = ?",
                       (addr,)).fetchone()
    if row is None:
        return {"error": f"no function at {addr}"}
    name, insns, leaf, tu_id = row
    total = conn.execute("select count(*) from attempts where func_addr = ?", (addr,)).fetchone()[0]
    attempt = conn.execute(
        "select id, score, exact, diff_summary, source_code, strategy, model, compiler_stderr "
        "from attempts where func_addr = ? and compiled = 1 order by score desc limit 1",
        (addr,)).fetchone()
    status = {"addr": addr, "name": name, "insns": insns, "leaf": bool(leaf), "tu_id": tu_id,
              "attempts": total}
    if attempt:
        rid, score, exact, diff, source, strategy, model, stderr = attempt
        status.update({"best_attempt_id": rid, "best_score": score, "exact": bool(exact),
                       "strategy": strategy, "model": model,
                       "faults": _profile(diff, score or 0.0, bool(exact)),
                       "diff_head": "\n".join((diff or "").splitlines()[:40]),
                       "source_chars": len(source or "")})
    else:
        status["note"] = "no compiling attempt yet; the residual is the whole target"
    return status


def score_candidate(ctx: Context, func: int, source: str, log: bool = True,
                    parent: int | None = None, run_id: str = "") -> dict:
    """Compile one candidate C for a function and report what the compiler and the diff say.

    This is the whole point of the server. The verdict is not a model's opinion: the candidate is
    compiled with the project's own recipe and compared against the target object, and the fault
    classes come from `solver/signals.py`. A model that calls this in a loop is hill-climbing against
    the binary rather than guessing once.

    LOGGING IS ON BY DEFAULT, and that default is the point. `CLAUDE.md` requires every attempt to be
    recorded, and `solver/workspace.py:470` records what happens otherwise: an entire day of ~250
    generations wrote ZERO rows and could not be recovered. An unlogged hill-climb is also exactly the
    data shape `TRAINING.md` calls unusable, because the explicit parent -> child edge is what turns a
    pair into refinement data -- and this tool could not write one at all before, since `connect()` is
    read-only and `parent_attempt_id` was never passed.

    `parent` is the attempt this candidate was derived from. Pass it whenever the model is editing a
    previous candidate: that is the edge `eval/distill_policy.py` trains on. Omit it for an
    independent draw, which is honest -- best-of-N samples have no parent, and inferring one from
    adjacent rows is forbidden.
    """
    name = _function_name(ctx, func)
    if name is None:
        return {"error": f"no function at {func}"}
    ws = _workspace(ctx, name)
    conn = None
    log_error = ""
    if log:
        try:
            conn = ctx.connect_write()
        except sqlite3.Error as exc:                                # noqa: PERF203
            log_error = f"{type(exc).__name__}: {exc}"
    attempt = workspace.score(ws, ctx.repo, name, source, conn=conn, func=name,
                              strategy="mcp:score_candidate", model="mcp-client",
                              run_id=run_id or "mcp", parent_attempt_id=parent,
                              relation="derive" if parent is not None else "",
                              action="score_candidate", run_kind="mcp")
    if conn is not None:
        conn.close()
    return {"func": func, "name": name, "compiled": attempt.compiled, "score": attempt.score,
            "exact": attempt.exact, "faults": _profile(attempt.diff, attempt.score, attempt.exact,
                                                       attempt.compiled),
            "diff_head": "\n".join((attempt.diff or "").splitlines()[:60]),
            "compiler_stderr_head": "\n".join((attempt.compiler_stderr or "").splitlines()[:20]),
            "logged": conn is not None,
            "receipt_id": getattr(attempt, "receipt_id", None),
            "parent_attempt_id": parent,
            "log_error": log_error}


def classify_residual(ctx: Context, diff: str, score: float = 0.0,
                      exact: bool = False, compiled: bool = True) -> dict:
    """Which KIND of wrong a diff is. The kind, not the amount, decides the next move."""
    verdict = signals.analyse(diff, score, exact, compiled)
    return {"faults": _profile(diff, score, exact, compiled),
            "repairable": verdict.repairable, "conditional_repair": verdict.conditional_repair,
            "no_repair_implemented": verdict.no_repair_implemented,
            "unrepairable": verdict.unrepairable,
            "diff_lines": verdict.diff_lines, "instr_delta": verdict.instr_delta}


def evidence_for(ctx: Context, func: int, limit: int = 40) -> dict:
    """The binary-derived facts about a function's memory accesses and calls.

    Evidence only: widths and offsets the binary actually performs. Absent fields stay absent, which
    is the project's rule -- `char unk_00[0x24];` beats an invented name.
    """
    conn = ctx.connect()
    rows = conn.execute(
        "select kind, addr, op, base, offset, width, signed, access, is_load, target_addr "
        "from evidence where func_addr = ? order by addr limit ?", (func, limit)).fetchall()
    columns = ("kind", "addr", "op", "base", "offset", "width", "signed", "access", "is_load",
               "target_addr")
    return {"func": func, "rows": [dict(zip(columns, row)) for row in rows],
            "note": "widths and offsets are what the binary does; nothing here is inferred"}


def practice_function(ctx: Context, family: str | None = None, seed: int = 0) -> dict:
    """A generated (assembly, C) pair whose answer is known, for practice with instant feedback."""
    try:
        from tools import synthetic_corpus as corpus
    except Exception as exc:
        return {"error": f"synthetic corpus unavailable: {exc}"}
    families = sorted(corpus.FAMILIES)
    if family is None:
        return {"families": families,
                "capabilities": {name: corpus.FAMILIES[name].capability for name in families}}
    if family not in corpus.FAMILIES:
        return {"error": f"unknown family {family}", "families": families}
    name, source = corpus.generate(family, seed)
    return {"family": family, "capability": corpus.FAMILIES[family].capability,
            "name": name, "source": source,
            "expectation": corpus.FAMILIES[family].expectation}


def trajectory_status(ctx: Context) -> dict:
    """How much improvement data exists. The model should be able to see the scarcity it is in."""
    conn = ctx.connect()
    both = conn.execute(
        "select count(*) from attempt_edges e join attempts p on p.id = e.parent_attempt_id "
        "join attempts c on c.id = e.child_attempt_id where p.compiled = 1 and c.compiled = 1"
    ).fetchone()[0]
    improving = conn.execute(
        "select count(*) from attempt_edges e join attempts p on p.id = e.parent_attempt_id "
        "join attempts c on c.id = e.child_attempt_id "
        "where p.compiled = 1 and c.compiled = 1 and c.score > p.score").fetchone()[0]
    parents = conn.execute(
        "select count(distinct e.parent_attempt_id) from attempt_edges e "
        "join attempts p on p.id = e.parent_attempt_id join attempts c on c.id = e.child_attempt_id "
        "where p.compiled = 1 and c.compiled = 1 and c.score > p.score").fetchone()[0]
    return {"edges_both_compiled": both, "improving_edges": improving,
            "functions_with_an_improving_edge": parents,
            "note": "improvement trajectories are the scarce input; every score_candidate call can "
                    "manufacture one"}


# --- wiring -------------------------------------------------------------------

_WORKSPACES: dict[str, Path] = {}


def _function_name(ctx: Context, addr: int) -> str | None:
    row = ctx.connect().execute("select name from functions where addr = ?", (addr,)).fetchone()
    return row[0] if row else None


def _workspace(ctx: Context, name: str) -> Path:
    """The per-function matching workspace, bootstrapped on first use and kept for the session."""
    if name not in _WORKSPACES:
        _WORKSPACES[name] = workspace.bootstrap(ctx.repo, name)
    return _WORKSPACES[name]


def build_server(context: Context | None = None):
    """The MCP server. Imported lazily so the tool functions stay usable without the SDK."""
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:                                  # pragma: no cover - transport only
        raise SystemExit(
            f"the MCP SDK is not installed ({exc}). Install with: pip install 'mcp<2'") from exc
    ctx = context or Context()
    server = FastMCP("gamedecomp")

    @server.tool()
    def list_functions_tool(limit: int = 20) -> str:
        """Candidate functions ranked by how much of the residual an implemented pass owns."""
        return json.dumps(list_functions(ctx, limit), indent=2)

    @server.tool()
    def function_status_tool(addr: int) -> str:
        """Target assembly summary, best attempt and current fault profile for one function."""
        return json.dumps(function_status(ctx, addr), indent=2)

    @server.tool()
    def score_candidate_tool(func: int, source: str, log: bool = True,
                             parent: int | None = None, run_id: str = "") -> str:
        """Compile C for this function and return score, exactness and fault classes.

        Every call is recorded as an attempt unless `log=False`. Pass `parent` (a previous
        attempt id) when editing that attempt's candidate: it writes the parent->child edge that
        the training path needs, and it is the only way that edge is ever created.
        """
        return json.dumps(score_candidate(ctx, func, source, log, parent, run_id), indent=2)

    @server.tool()
    def classify_residual_tool(diff: str, score: float = 0.0, exact: bool = False,
                               compiled: bool = True) -> str:
        """Classify a diff by the KIND of wrong it is."""
        return json.dumps(classify_residual(ctx, diff, score, exact, compiled), indent=2)

    @server.tool()
    def evidence_for_tool(func: int, limit: int = 40) -> str:
        """Binary-derived memory-access and call facts for a function. Never a guessed field."""
        return json.dumps(evidence_for(ctx, func, limit), indent=2)

    @server.tool()
    def practice_function_tool(family: str | None = None, seed: int = 0) -> str:
        """A generated (assembly, C) practice pair with a known answer, or the family list."""
        return json.dumps(practice_function(ctx, family, seed), indent=2)

    @server.tool()
    def trajectory_status_tool() -> str:
        """How much improvement data exists, and what would create more."""
        return json.dumps(trajectory_status(ctx), indent=2)

    return server


def main() -> int:
    build_server().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
