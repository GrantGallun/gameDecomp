"""Package a function the local model could not solve, for a stronger model.

The tier split: local inference does the bulk cheaply, and the cases it cannot
crack get escalated. Escalation is only worth its cost if the hard case arrives
complete -- target, every attempt, every diff, and which catalogued idioms
fired -- so the stronger model starts where the local one stopped instead of
from scratch.

Everything here comes from the KB and the workspace. Ground-truth source is
NEVER included: the point is to solve the function, and a case file containing
the answer would be worthless as a test and dangerous as a habit.

Run:
    python3 -m solver.escalate --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \\
        --function randomNextSecondary --out cases/
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from patterns.catalog import hints_for_asm
from solver import workspace

TEMPLATE = """\
# Escalated case: {func}

The local model exhausted its budget without a byte-exact match.

- Best score reached: **{best_score:.2f}%**
- Attempts made: {n_attempts}
- Model: {models}

## Target assembly

```mips
{asm}
```

## m2c draft

```c
{draft}
```

## Catalogued idioms detected in this target

{hints}

## Best attempt ({best_score:.2f}%)

```c
{best_code}
```

## Instruction diff for that attempt

`-` is what the TARGET expects, `+` is what the attempt produced.

```diff
{best_diff}
```

## What was already tried

{tried}

## Task

Produce C that compiles byte-exact under IDO 5.3 `-O2 -mips1`.

Constraints the harness enforces:
- only `#include "common.h"`; all other types and externs inline
- `common.h` already defines u8/s8/u16/s16/u32/s32/u64/s64/f32/f64
- C89: declarations at the start of a block
- never write `do` (the build rejects the token); a post-tested loop is
  for (;;) { body; if (!cond) break; }, NOT while (cond) { body }
- no inline asm, no GLOBAL_ASM/INCLUDE_ASM

Verify with `./build.sh <file>.c` in `nonmatchings/{func}/`.
"""


def build_case(repo: Path, conn: sqlite3.Connection, func: str) -> str:
    ws = workspace.bootstrap(repo, func)
    asm = workspace.target_asm(ws, func)
    draft = workspace.m2c_draft(ws)

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT a.* FROM attempts a JOIN functions f ON f.addr = a.func_addr"
        " WHERE f.name = ? ORDER BY a.score DESC, a.id DESC", (func,)).fetchall()

    if not rows:
        raise SystemExit(f"no attempts logged for {func} -- run the solver first")

    best = rows[0]
    models = ", ".join(sorted({r["model"] for r in rows if r["model"]}))

    tried = []
    for r in sorted(rows, key=lambda r: r["id"]):
        state = ("EXACT" if r["score"] == 100 else
                 f"{r['score']:.2f}%" if r["compiled"] else
                 f"did not compile ({(r['compiler_stderr'] or '')[:80]})")
        tried.append(f"- attempt {r['iteration']} [{r['strategy']}]: {state}")

    return TEMPLATE.format(
        func=func, best_score=best["score"], n_attempts=len(rows), models=models,
        asm=asm, draft=draft.strip() or "(none)",
        hints=hints_for_asm(asm).strip() or "(none detected)",
        best_code=best["source_code"].strip(),
        best_diff=(best["diff_summary"] or "(attempt did not compile)").strip()[:4000],
        tried="\n".join(tried),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--function", required=True)
    ap.add_argument("--out", type=Path, default=Path("cases"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db.expanduser())
    case = build_case(args.repo.expanduser(), conn, args.function)

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"{args.function}.md"
    path.write_text(case)
    print(f"wrote {path} ({len(case)} bytes)")


if __name__ == "__main__":
    main()
