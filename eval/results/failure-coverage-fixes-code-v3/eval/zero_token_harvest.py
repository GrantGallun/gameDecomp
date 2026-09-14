"""Score the m2c bootstrap draft as-is, for functions nobody has ever tried.

No model, no tokens, no project headers -- just the draft the bootstrap
already produces, handed straight to the oracle. On trivially-shaped functions
(empty bodies, single returns, thin wrappers) m2c is frequently already exact,
and the only reason those were not matched is that nothing ever compiled the
draft.

Motivating residual: `tools/claude` classified any 5-10 character alphanumeric
argument as a decomp.me scratch ID and refused it before bootstrap. Existence
beats shape -- 70 functions with real assembly under asm/ had zero attempts in
the KB and had never appeared as failures anywhere, because they had never
appeared at all. `--unblocked` selects exactly that class.

Deliberately NOT included: solver/project_headers.py. Adding a reconstructed
include/game header hands over the decomp team's own prototype and struct
layout, which is a different experiment with a different contamination
posture. The CANDIDATE here includes only common.h, the same as every other
baseline.

That is a statement about the candidate's includes, NOT a contamination
guarantee. m2c builds its context by preprocessing a project translation unit,
so reconstructed include/game/** MEMBER NAMES are already present in the draft:
MusAsk's draft reads `var_v1->pdata` and `->soundId`, both declared in
include/game/audio/audio_engine.h. The leak is names only -- no layout or
offset enters the candidate, and the oracle still decides -- but it is a leak,
and it is why the largest remaining leaf bucket cannot be closed
deterministically. See the hypothesis bank:
m2c-drafts-already-carry-reconstructed-header-knowledge.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

from eval import matched
from solver import (buildtypes, compilefix, globaldecl, llm, memberaccess,
                    typedecl, typepool, unknowns, workspace)
from tools import score_repo_function

# The exact shape tools/claude mistook for a decomp.me scratch ID.
SCRATCH_SHAPED = re.compile(r"^[A-Za-z0-9]+$")
STRATEGY = "zero-token-m2c-harvest"


def scratch_shaped(name: str) -> bool:
    return bool(SCRATCH_SHAPED.match(name)) and 5 <= len(name) <= 10


def heldout_names(sets_dir: Path) -> set[str]:
    """Every frozen held-out name, from every split. Never attempted here.

    The split files key entries by "function", not "name". The first version
    of this read only "name", so it returned the empty set against the real
    eval/sets/*.json and silently disabled the one filter that must never be
    wrong -- and its unit test passed, because the test built its own fixture
    in the shape the parser expected rather than the shape on disk. Both keys
    are accepted now and test_heldout_matches_the_real_split_files reads the
    actual files. A guard that cannot fail loudly has to be checked against
    production data, not a mock.
    """
    names: set[str] = set()
    for path in sorted(sets_dir.glob("sbk1_v*.json")):
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(blob, dict):
            continue
        for entry in blob.get("heldout") or []:
            name = entry if isinstance(entry, str) else (
                entry.get("function") or entry.get("name"))
            if isinstance(name, str):
                names.add(name)
    return names


def candidates(conn: sqlite3.Connection, sets_dir: Path, *,
               unblocked_only: bool, explicit: list[str]) -> list[str]:
    known = [row[0] for row in conn.execute("select name from functions")]
    attempted = {row[0] for row in conn.execute(
        "select distinct f.name from attempts a"
        " join functions f on f.addr=a.func_addr")}
    excluded = heldout_names(sets_dir) | matched.already_matched(conn)
    if explicit:
        wanted = [n for n in explicit if n not in excluded]
    else:
        wanted = [n for n in known
                  if n not in excluded and n not in attempted
                  and (not unblocked_only or scratch_shaped(n))]
    return sorted(wanted)


DO_BLOCK = re.compile(r"\bdo\s*\{")


def apply_fix(name, conn, code, func, known_types, pool, symbols):
    """Run one registered fix. Returns (code, changed)."""
    if name == "do-while":
        if not DO_BLOCK.search(code):
            return code, False
        try:
            out = score_repo_function.rewrite_do_while(code)
        except ValueError:
            return code, False
        return out, out != code
    if name == "byte-index":
        out, plans = memberaccess.rewrite(code, func)
        return out, bool(plans)
    if name == "typedecl":
        out, plans = typedecl.synthesize(conn, func, code, known_types, pool)
        return out, bool(plans)
    if name == "globals":
        if not symbols:
            return code, False
        out, plans = globaldecl.declare(conn, code, func, known_types, symbols)
        return out, bool(plans)
    if name == "strip-includes":
        out = llm.strip_unresolvable_includes(code)
        return out, out != code
    if name == "strip-redeclarations":
        out, dropped = buildtypes.strip_redeclarations(code, known_types)
        return out, bool(dropped)
    return code, False


def iterate(attempt, conn, draft, func, known_types, pool, symbols,
            max_steps: int = 6) -> tuple[object, str, list[dict]]:
    """Error-directed greedy repair: fix the CURRENT error, recompile, repeat.

    The blind chain applies every fix once in a fixed order. That works, but it
    cannot react to what a repair reveals -- and compile errors are reported one
    at a time, so the second blocker is invisible until the first is gone. This
    is the shape Manifold's selection phase uses (Superset Decompilation, arXiv
    2603.28002): the compiler is the oracle and each step reduces the error
    count.

    Every step is recorded, including the signature that routed it and the
    signatures nothing routed -- an unroutable error is a named gap, not a
    silent stop.
    """
    code = draft
    trace: list[dict] = []
    best = attempt("m2c", code)
    if best.compiled:
        return best, code, trace
    for step in range(max_steps):
        sig, fixes = compilefix.dispatch(best.compiler_stderr, code, func,
                                         known_types)
        entry = {"step": step, "signature": sig,
                 "fixes": [f.name for f in fixes]}
        if not fixes:
            entry["outcome"] = "no registered fix"
            trace.append(entry)
            break
        moved = None
        for fix in fixes:
            candidate, changed = apply_fix(fix.name, conn, code, func,
                                           known_types, pool, symbols)
            if changed:
                moved = fix.name
                code = candidate
                break
        if moved is None:
            entry["outcome"] = "every registered fix declined"
            trace.append(entry)
            break
        entry["applied"] = moved
        att = attempt(f"iter{step}-{moved}", code)
        entry["compiled"] = att.compiled
        entry["score"] = att.score
        trace.append(entry)
        best = att
        if att.compiled:
            break
    return best, code, trace


def repair_chain(conn, func: str, draft: str, known_types: set[str],
                 pool: dict | None = None, symbols: dict | None = None
                 ) -> tuple[str, list[str], list[dict], str]:
    """Every zero-token source repair that applies, composed in one candidate.

    Returns (code, applied_stage_names, typedecl_plans, do_while_decline).
    Pure with respect to the workspace so it can be tested without a build:
    the compile-failure taxonomy this fixes was measured, and the fixes must
    be shown to FIRE on it rather than only to decline elsewhere.
    """
    code, applied, declined = draft, [], ""
    # `do` is banned by the per-function build.sh, but the ban is on the
    # TOKEN, not the control-flow shape -- for(;;){ body; if (!cond) break; }
    # is the sanctioned spelling, CONFIRMED across 389 of 390 sites.
    if DO_BLOCK.search(code):
        try:
            rewritten = score_repo_function.rewrite_do_while(code)
            if rewritten != code:
                code = rewritten
                applied.append("do-while")
        except ValueError as exc:          # continue/unbalanced: decline
            declined = str(exc)[:120]
    # Before declaring anything: a byte pointer with member accesses needs
    # INDEXING, not a struct. Synthesising a type there would rescale the
    # pointer arithmetic these functions depend on.
    indexed, index_plans = memberaccess.rewrite(code, func)
    if index_plans:
        code = indexed
        applied.append("byte-index")
    typed, plans = typedecl.synthesize(conn, func, code, known_types, pool)
    if plans:
        code = typed
        applied.append("typedecl")
    # Last, so it sees any name the earlier stages introduced. The largest
    # surviving blocker after typedecl on the leaf residual: 43 of ~161.
    if symbols:
        declared, global_plans = globaldecl.declare(
            conn, code, func, known_types, symbols)
        if global_plans:
            code = declared
            applied.append("globals")
    return code, applied, plans, declined


def harvest_one(repo: Path, conn: sqlite3.Connection, func: str,
                run_id: str, known_types: set[str] | None = None,
                pool: dict | None = None, symbols: dict | None = None,
                iterative: bool = False) -> dict:
    row: dict = {"function": func, "stages": []}
    started = time.time()
    try:
        ws = workspace.bootstrap(repo, func)
    except Exception as exc:  # bootstrap failures are data, not crashes
        row.update(status="bootstrap_failed", error=str(exc)[:400])
        return row
    draft = workspace.m2c_draft(ws)
    if not draft.strip():
        row.update(status="no_draft")
        return row

    def attempt(stage: str, code: str):
        att = workspace.score(ws, repo, f"{run_id}_{func}_{stage}", code,
                              conn=conn, func=func,
                              strategy=f"{STRATEGY}-{stage}",
                              run_id=run_id, token_cost=0)
        row["stages"].append({"stage": stage, "compiled": att.compiled,
                              "score": att.score, "exact": att.exact,
                              "compiler_error": att.compiler_stderr[:200]})
        return att

    if iterative and known_types is not None:
        best, best_code, trace = iterate(attempt, conn, draft, func,
                                         known_types, pool, symbols)
        row["trace"] = trace
        row.update(status="scored", compiled=best.compiled, score=best.score,
                   exact=best.exact,
                   wall_seconds=round(time.time() - started, 3))
        if not best.compiled:
            last = row["stages"][-1]
            row["compiler_error"] = (last["compiler_error"]
                                     or best.compiler_stderr)[:300]
            row["error_stage"] = last["stage"]
        if best.exact:
            row["exact_source"] = best_code
        return row

    best = attempt("m2c", draft)
    best_code = draft
    # Stages two and three are both zero-token source repairs for compile
    # failures that are not wrong answers. Measured over 174 never-attempted
    # leaves, the non-compiling drafts split into: 48 undeclared struct type,
    # 33 the build's do-token ban, 27 undeclared symbol, 26 type conflict.
    # The first two already have deterministic fixes in this repo.
    if not best.compiled and known_types is not None:
        code, applied, plans, declined = repair_chain(
            conn, func, draft, known_types, pool, symbols)
        row["typedecl_plans"] = [
            {k: v for k, v in p.items() if k != "text"} for p in plans]
        if declined:
            row["do_while_declined"] = declined
        if applied:
            att = attempt("+".join(applied), code)
            if att.score > best.score or (att.compiled and not best.compiled):
                best, best_code = att, code

    row.update(status="scored", compiled=best.compiled, score=best.score,
               exact=best.exact, wall_seconds=round(time.time() - started, 3))
    if not best.compiled:
        # Report the LAST stage's error, not the best attempt's. When a repair
        # stage also fails to compile, both score 0.0, so `best` stays the m2c
        # attempt and the receipt showed the PRE-repair error -- which made 38
        # leaves look like they were still blocked on the do-token after the
        # rewrite had already removed it. The informative error is the one the
        # repaired candidate produced.
        last = row["stages"][-1]
        row["compiler_error"] = (last["compiler_error"] or
                                 best.compiler_stderr)[:300]
        row["error_stage"] = last["stage"]
    if best.exact:
        row["exact_source"] = best_code
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="~/decomp/sbk1")
    ap.add_argument("--db", default="~/decomp/kb-sbk1.sqlite")
    ap.add_argument("--sets", default="eval/sets")
    ap.add_argument("--unblocked", action="store_true",
                    help="only names tools/claude refused as scratch IDs")
    ap.add_argument("--names", nargs="*", default=[])
    ap.add_argument("--names-file",
                    help="one function name per line; for lists too long for"
                         " a command line")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--iterate", action="store_true",
                    help="dispatch on the compile error via solver/compilefix.py, recompiling after each fix, instead of applying the blind chain")
    ap.add_argument("--typedecl", action="store_true",
                    help="on a compile failure, declare undeclared param"
                         " structs from binary evidence (still zero tokens)")
    ap.add_argument("--out", default="eval/results/zero_token_harvest.json")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser()
    conn = sqlite3.connect(str(Path(args.db).expanduser()), timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")

    explicit = list(args.names)
    if args.names_file:
        explicit += [line.strip() for line
                     in Path(args.names_file).read_text().splitlines()
                     if line.strip()]
    names = candidates(conn, Path(args.sets),
                       unblocked_only=args.unblocked, explicit=explicit)
    if args.limit:
        names = names[:args.limit]
    run_id = f"zth-{int(time.time())}"
    receipt = {
        "schema_version": 1,
        "kind": "zero_token_m2c_harvest",
        "run_id": run_id,
        "policy": {
            "generation_tokens": 0,
            "project_headers_used": False,
            "target_source_read": False,
            "heldout_excluded": True,
            "typedecl_stage": bool(args.typedecl),
            "iterative": bool(args.iterate),
        },
        "selected": names,
        "rows": [],
        "started_at": int(time.time()),
    }
    known_types = buildtypes.type_names(repo) if args.typedecl else None
    pool = None
    if args.typedecl:
        # Built once over EVERY bootstrapped draft, not just the selection:
        # a type's layout is only as complete as the set of functions that
        # contributed to it, and restricting that set to the run's own targets
        # throws away the whole point.
        corpus = sorted(p.name for p in (repo / "nonmatchings").iterdir()
                        if (p / "base.c").is_file())
        pool = typepool.pool(conn, typepool.type_uses(repo, corpus),
                             known_types)
        receipt["pool"] = {"types": len(pool),
                           "offsets": sum(len(v) for v in pool.values()),
                           "corpus_drafts": len(corpus)}
        print(f"pooled {len(pool)} types from {len(corpus)} drafts", flush=True)
    symbols = unknowns.symbol_table(repo) if args.typedecl else None
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for index, func in enumerate(names, 1):
        row = harvest_one(repo, conn, func, run_id, known_types, pool,
                          symbols, args.iterate)
        receipt["rows"].append(row)
        flag = "EXACT" if row.get("exact") else row.get("status", "")
        print(f"[{index}/{len(names)}] {func}: {flag} "
              f"score={row.get('score', 0)}", flush=True)
        # Written every row, not at the end: a sweep over a thousand functions
        # takes long enough that it will be interrupted, and a receipt that
        # only exists on clean exit loses the whole run. The attempts table is
        # the durable record either way, but the receipt is what gets read.
        out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    exact = [r["function"] for r in receipt["rows"] if r.get("exact")]
    receipt["summary"] = {
        "selected": len(names),
        "bootstrapped": sum(r.get("status") == "scored"
                            for r in receipt["rows"]),
        "compiled": sum(bool(r.get("compiled")) for r in receipt["rows"]),
        "exact": len(exact),
        "exact_functions": exact,
        "charged_generation_tokens": 0,
        "typedecl_planned": sum(bool(r.get("typedecl_plans"))
                                for r in receipt["rows"]),
        "repair_rescued": sum(
            1 for r in receipt["rows"]
            for s in r.get("stages", [])
            if s["stage"] != "m2c" and s["compiled"]),
    }
    receipt["completed_at"] = int(time.time())
    out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt["summary"], indent=2))


if __name__ == "__main__":
    main()
