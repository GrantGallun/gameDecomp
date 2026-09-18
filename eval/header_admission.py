"""Admission route for the never-attempted drafts: include the header that declares the type.

Established mechanically rather than reasoned: the drafts die on declarations like
`PlayerCommandState *var_s0;`, and `PlayerCommandState` IS fully declared in the project tree --
`include/game/audio/audio_engine.h` lines 61-154, a complete `typedef struct`. So the terminal
`Syntax Error` / `Empty declaration specifiers` is a missing include, not an unattributable name, and
`typedecl` was right to decline: declaring the struct from a member name it cannot place would have been
"a guess dressed as a layout", while the real declaration is already written down one directory away.

WHAT THIS IS AND IS NOT. A candidate that only builds after being handed a reconstructed
`include/game/**` header is HEADER-ASSISTED by this project's own taxonomy -- neither a copied body nor
something reachable from binary evidence -- so it is reported as ADMISSION and never as a match. The
point of the route is different: 1,611 functions have no attempt at all, and an attempt is what the
knowledge base needs in order to learn anything about them.

    python3 eval/header_admission.py [--repo ~/decomp/sbk1] [--limit N] [--rounds 3]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solver import workspace                                              # noqa: E402

REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1.sqlite"
# A local declaration whose type is a capitalised name that the draft never defines.
LOCAL_DECL = re.compile(r"^\s*(?P<type>[A-Z]\w*)\s*(?P<stars>\**)\s*(?P<name>[A-Za-z_]\w*)\s*;\s*$", re.M)
INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)
PRIMITIVE = {"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64", "int", "short",
             "char", "long", "float", "double", "void", "unsigned", "signed"}


def declaring_header(repo: Path, type_name: str) -> str | None:
    """The include-relative path of a header that declares `type_name`, or None.

    Two spellings count as a declaration: `typedef struct Name {` (a tagged struct) and `} Name;`
    (the closing typedef), because the project writes both and a header may introduce the type by
    either. Searching the tree is the whole evidence base here -- no inference about what the type
    "should" be.
    """
    patterns = (re.compile(rf"\btypedef\s+struct\s+{re.escape(type_name)}\b"),
                re.compile(rf"\}}\s*{re.escape(type_name)}\s*;"))
    names = set()
    for header in sorted((repo / "include").rglob("*.h")):
        try:
            text = header.read_text(errors="replace")
        except OSError:
            continue
        if any(p.search(text) for p in patterns):
            names.add(str(header.relative_to(repo / "include")))
    return sorted(names)[0] if names else None


def missing_types(draft: str) -> list[str]:
    defined = set(re.findall(r"\btypedef\s+struct\s+(\w+)", draft)) | \
        set(re.findall(r"\}\s*(\w+)\s*;", draft))
    out = []
    for match in LOCAL_DECL.finditer(draft):
        name = match.group("type")
        if name in PRIMITIVE or name in defined or name in out:
            continue
        out.append(name)
    return out


def add_includes(repo: Path, draft: str, headers: list[str]) -> str:
    """Insert includes after the last existing one, so ordering stays as the draft wrote it."""
    existing = INCLUDE.findall(draft)
    block = "".join(f'#include "{h}"\n' for h in headers if h not in existing)
    if not block:
        return draft
    matches = list(INCLUDE.finditer(draft))
    if matches:
        at = matches[-1].end()
        return draft[:at] + "\n" + block + draft[at:]
    return block + draft


def run_one(conn, repo: Path, name: str, *, rounds: int, context: dict | None = None) -> dict:
    row: dict = {"function": name}
    ws = workspace.bootstrap(repo, name)
    draft_path = ws / "base.c"
    if not draft_path.is_file():
        return dict(row, status="no-draft")
    draft = draft_path.read_text(errors="replace")
    row["draft_bytes"] = len(draft)
    tried: list[str] = []
    for attempt in range(rounds + 1):
        att = workspace.score(ws, repo, name, draft, conn=conn, func=name,
                              strategy=f"header-admission:round{attempt}", iteration=0,
                              run_kind="header-admission")
        row.update(baseline_compiled=bool(att.compiled) if attempt == 0 else row.get("baseline_compiled"),
                   compiled=bool(att.compiled), exact=bool(att.exact), score=att.score,
                   rounds_used=attempt, includes=tried)
        if att.compiled:
            row["status"] = "exact" if att.exact else "compiled"
            row["error"] = None
            return row
        row["error"] = (att.compiler_stderr or "").strip().splitlines()[:3]
        if attempt >= rounds:
            break
        wanted = [t for t in missing_types(draft) if t not in tried]
        headers = [h for h in (declaring_header(repo, t) for t in wanted) if h]
        headers = [h for h in headers if h not in tried]
        if not headers:
            # BREAK, not return. Returning here skipped the repair-chain stage below -- which owns the
            # error this stage exposes -- and that is exactly how the first version reported
            # 'no-declaring-header' on all seven while compiling none.
            row["unresolved_types"] = wanted[:6]
            break
        tried.extend(headers)
        draft = add_includes(repo, draft, headers)
    # SECOND STAGE, because the header stage moves the failure rather than clearing it. Measured on
    # MusStartEffect: after `game/audio/audio_engine.h` resolves `PlayerCommandState`, the terminal
    # errors become `'mus_channels' undefined`, `'max_channels' undefined`, `'gSoundPriorityTable'
    # undefined` -- the class `solver.compilefix` ALREADY registers as
    # "X undefined; reoccurrences will not be reported." -> GLOBALS (`solver.globaldecl`), and which
    # `zero_token_harvest.repair_chain` already applies. Running it on the ORIGINAL draft could not
    # help, because the draft did not parse yet; running it here, after the types resolve, is the
    # composition that was missing.
    try:
        from eval import zero_token_harvest as zth
        from solver import buildtypes, typepool, unknowns
        known = buildtypes.type_names(repo)
        corpus = sorted(p.name for p in (repo / "nonmatchings").iterdir() if (p / "base.c").is_file())
        ctx = context or {}
        pool = ctx.get("pool")
        if pool is None:
            pool = typepool.pool(conn, typepool.type_uses(repo, corpus), known)
        symbols = ctx.get("symbols")
        if symbols is None:
            symbols = unknowns.symbol_table(repo)
        repaired, stages, _plans, declined = zth.repair_chain(conn, name, draft, known, pool, symbols)
        row["harvest_stages"] = stages
        if declined:
            row["harvest_declined"] = declined
        if repaired != draft:
            att = workspace.score(ws, repo, name, repaired, conn=conn, func=name,
                                  strategy="header-admission:repair-chain", iteration=0,
                                  run_kind="header-admission")
            row.update(compiled=bool(att.compiled), exact=bool(att.exact), score=att.score,
                       rounds_used=rounds)
            if att.compiled:
                row["status"] = "exact" if att.exact else "compiled"
                row["error"] = None
                row["admitted_by"] = "header+repair-chain"
                return row
            row["error"] = (att.compiler_stderr or "").strip().splitlines()[:3]
    except Exception as exc:                                             # noqa: BLE001
        row["harvest_error"] = f"{type(exc).__name__}: {exc}"
    row["status"] = "not-compiling"
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--out", type=Path, default=ROOT / "eval/results/header-admission-20260917")
    ap.add_argument("--functions", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=0.0)
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(args.db))
    if args.functions:
        names = [n.strip() for n in args.functions.split(",") if n.strip()]
    else:
        # Workspace directories may carry a numeric suffix (`__MusIntPowerOf2-2`), and
        # `workspace.bootstrap` refuses anything but `[A-Za-z_]\w*` -- so the FUNCTION name is the
        # directory name with the suffix removed. Taking the directory name verbatim raised
        # `ValueError: invalid function identifier` on those and skipped them; measured on the first
        # pass, which is how this was found.
        names = [re.sub(r"-\d+$", "", n.name) for n in sorted((args.repo / "nonmatchings").iterdir())
                 if (n / "base.c").is_file()
                 and not conn.execute(
                     "select 1 from attempts a join functions f on f.addr = a.func_addr "
                     "where f.name = ? and a.compiled = 1 limit 1",
                     (re.sub(r"-\d+$", "", n.name),)).fetchone()]
    if args.limit:
        names = names[:args.limit]
    print(f"never-compiling functions with an m2c draft: {len(names)}", flush=True)

    state_path = args.out / "state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else {}
    started = time.time()
    for name in names:
        if name in state:
            continue
        if args.seconds and (time.time() - started) > args.seconds:
            print("BUDGET reached", flush=True)
            break
        try:
            state[name] = run_one(conn, args.repo, name, rounds=args.rounds)
        except Exception as exc:                                         # noqa: BLE001
            state[name] = {"function": name, "status": "raised",
                           "error": f"{type(exc).__name__}: {exc}"}
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        row = state[name]
        print(f"[{len(state)}/{len(names)}] {name:<44} {row.get('status'):<22} "
              f"score={row.get('score')} includes={row.get('includes')}", flush=True)

    counts = collections.Counter(r.get("status") for r in state.values())
    compiled = sorted(n for n, r in state.items() if r.get("compiled"))
    exact = sorted(n for n, r in state.items() if r.get("exact"))
    summary = {"population": len(names), "recorded": len(state), "status_counts": dict(counts),
               "compiled": len(compiled), "exact": len(exact), "exact_functions": exact}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "exact_functions"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
