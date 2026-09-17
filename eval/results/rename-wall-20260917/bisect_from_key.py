"""Bisect from the KEY back to our candidate: which edit is the one that breaks it?

The reference body for this function is in the build tree, so the whole residual has a known cause:
our candidate is a derivative of it. This compiles, in order,

    K0  the reference body verbatim            (expected: build refuses `do`)
    K1  reference body + do->for-break rewrite (the sanctioned lowering)
    K2  K1 + nothing else

and reports the object comparison for each, so the FIRST step that stops being exact is the culprit.
This uses the reference source for DIAGNOSIS only -- ground truth is for checking, never feeding --
and it compiles with conn=None so nothing is logged as a solved match.

    python3 eval/results/rename-wall-20260917/bisect_from_key.py <function> <candidate_attempt_id>
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import signals, workspace  # noqa: E402
from tools.score_repo_function import rewrite_do_while  # noqa: E402

DB = Path.home() / "decomp" / "kb-sbk1.sqlite"
REPO = Path.home() / "decomp" / "sbk1"
AXES = ("structural", "layout", "reloc", "regalloc", "ordering", "immediate")


def reference_body(function: str) -> str:
    """The function's own text from the reference tree, brace-matched."""
    for path in REPO.joinpath("src").rglob("*.c"):
        text = path.read_text(errors="replace")
        match = re.search(r"^[A-Za-z_][^\n(]*\b%s\s*\(" % re.escape(function), text, re.M)
        if not match:
            continue
        start = text.find("{", match.start())
        if start < 0:
            continue
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[match.start():index + 1], str(path)
    raise SystemExit(f"reference body for {function} not found under {REPO}/src")


def replace_body(candidate: str, function: str, body: str) -> str:
    """Swap the candidate's definition of `function` for `body`, keeping its preamble."""
    match = re.search(r"^[A-Za-z_][^\n(]*\b%s\s*\(" % re.escape(function), candidate, re.M)
    if not match:
        raise SystemExit("candidate definition not found")
    start = candidate.find("{", match.start())
    depth = 0
    for index in range(start, len(candidate)):
        if candidate[index] == "{":
            depth += 1
        elif candidate[index] == "}":
            depth -= 1
            if depth == 0:
                return candidate[:match.start()] + body + candidate[index + 1:]
    raise SystemExit("candidate body unbalanced")


def main() -> int:
    function = sys.argv[1]
    attempt = int(sys.argv[2])
    conn = sqlite3.connect(DB)
    candidate = conn.execute("select source_code from attempts where id = ?", (attempt,)).fetchone()[0]
    body, source_path = reference_body(function)
    print(f"reference body: {source_path} ({len(body.splitlines())} lines)")

    ws = workspace.bootstrap(REPO, function)
    variants = [("K0 reference body verbatim", replace_body(candidate, function, body))]
    lowered = rewrite_do_while(variants[0][1])
    variants.append(("K1 reference body + do->for-break", lowered))
    # The pipeline's own next step on that source: `single_use` inlines the guard local, which is
    # what turned the 99.395 baseline into the stored 99.936 candidate.
    inlined = lowered.replace("    shouldDraw = 1;\n", "", 1).replace("if (shouldDraw)", "if ((1))", 1)
    variants.append(("K2 = K1 + shouldDraw inlined", inlined))
    variants.append(("C  stored candidate (attempt %d)" % attempt, candidate))
    for label, text in variants:
        att = workspace.score(ws, REPO, function, text, conn=None)
        if not att.compiled:
            stderr = (att.compiler_stderr or "").strip().splitlines()
            print(f"{label:<46} NOT COMPILED  {stderr[-1][:90] if stderr else ''}")
            continue
        profile = signals.analyse(att.diff or "", att.score or 0.0, bool(att.exact), True)
        axes = {a: getattr(profile, a) for a in AXES}
        print(f"{label:<46} exact={att.exact} score={att.score:.3f} axes={axes}")
        if att.exact:
            continue
        shown = 0
        for line in (att.diff or "").splitlines():
            if line.startswith(("-", "+")) and not line.startswith(("---", "+++")):
                print(f"      {line}")
                shown += 1
                if shown >= 8:
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
