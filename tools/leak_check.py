"""Did a fetched page contain the answer? The judge's backstop for solvers with web access.

    verdict = leak(answer_sources, fetched_text)   # {"leak": bool, "raw": n, "structural": n, "lines": f, ...}

The compiler certifies a COPIED answer exactly like a derived one, so a solver that finds the public decomp has not
solved anything, and nothing downstream would notice. Every page a solver reads is compared with the task's answer
in all its spellings (the public original AND the anonymized one), three ways:

  raw          a run of RAW_K consecutive C tokens shared verbatim (a pasted function)
  structural   a run of STRUCT_K tokens shared after every identifier becomes `ID` (a copy with names changed)
  lines        the share of the answer's substantive statement lines present verbatim (a partial copy)

Pass `common` (common_grams over a background corpus) so shared idioms do not count. Measured 2026-10-05 on 150 public
functions against 40-function pages from the SAME game: 3 false positives (all line-share, short functions), every
verbatim copy caught, 122 of 150 renamed copies caught.

A leak voids the attempt: it scores as a leak, never as a solve, and the leak rate is reported per arm. Thresholds
are deliberately low (a shared 12-token run of real code is rare between different functions); a false positive
costs one attempt, a false negative costs the measurement.
"""
from __future__ import annotations

import re

RAW_K = 12
STRUCT_K = 24
LINE_SHARE = 0.3
TOKEN = re.compile(r"0[xX][0-9a-fA-F]+|\d+(?:\.\d*)?[fFuUlL]*|[A-Za-z_]\w*|->|<<=|>>=|&&|\|\||[-+*/%&|^!=<>]=|"
                   r"\+\+|--|<<|>>|[{}()\[\];,.?:~!<>=+\-*/%&|^]")
KEYWORDS = {"if", "else", "for", "while", "do", "return", "switch", "case", "default", "break", "continue", "goto",
            "sizeof", "struct", "union", "enum", "typedef", "static", "extern", "const", "volatile", "void", "char",
            "short", "int", "long", "float", "double", "signed", "unsigned"}
COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)


def tokens(text: str) -> list[str]:
    return TOKEN.findall(COMMENT.sub(" ", text or ""))


def _structural(toks: list[str]) -> list[str]:
    return ["ID" if re.match(r"[A-Za-z_]", t) and t not in KEYWORDS else t for t in toks]


def _shared_runs(answer: list[str], page: list[str], k: int, common: set | None = None) -> int:
    """How many k-token windows of the answer occur in the page, not counting windows in `common` (idioms that
    other functions share, which say nothing about THIS answer)."""
    if len(answer) < k or len(page) < k:
        return 0
    grams = {tuple(page[i:i + k]) for i in range(len(page) - k + 1)}
    return sum(g in grams and g not in (common or ()) for g in
               (tuple(answer[i:i + k]) for i in range(len(answer) - k + 1)))


def common_grams(functions: list[str]) -> set:
    """Token windows (raw and structural) that occur in at least two of `functions`: a background corpus's idioms.
    Measured on public decomps: without this, a 40-function page from the same game tripped the raw/structural
    tests in 5-20% of unrelated cases (shared display-list and bounds-check boilerplate)."""
    seen, common = set(), set()
    for text in functions:
        toks = tokens(text)
        mine = {tuple(toks[i:i + RAW_K]) for i in range(len(toks) - RAW_K + 1)}
        st = _structural(toks)
        mine |= {tuple(st[i:i + STRUCT_K]) for i in range(len(st) - STRUCT_K + 1)}
        common |= mine & seen
        seen |= mine
    return common


def _lines(answer: str) -> list[str]:
    out = []
    for line in answer.splitlines():
        s = " ".join(line.split())
        if len(s) >= 16 and s not in ("{", "}") and not s.startswith(("//", "/*")):
            out.append(s)
    return out


def leak(answer_sources: list[str], fetched: str, common: set | None = None) -> dict:
    page = tokens(fetched)
    page_struct = _structural(page)
    flat_page = " ".join(fetched.split())
    best = {"raw": 0, "structural": 0, "lines": 0.0}
    for src in answer_sources:
        toks = tokens(src)
        best["raw"] = max(best["raw"], _shared_runs(toks, page, RAW_K, common))
        best["structural"] = max(best["structural"], _shared_runs(_structural(toks), page_struct, STRUCT_K, common))
        lines = _lines(src)
        if lines:
            best["lines"] = max(best["lines"], round(sum(s in flat_page for s in lines) / len(lines), 3))
    best["leak"] = best["raw"] > 0 or best["structural"] > 0 or best["lines"] >= LINE_SHARE
    return best
