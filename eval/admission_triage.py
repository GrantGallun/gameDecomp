"""Split the 199 never-compiled functions by WHO failed: the pipeline, or the model.

Why this exists. `eval/results/admission-20260916/RESULT.md` established that the largest failure
population in the project is not residuals but admission: 199 functions that have never compiled
once, more than twice the live set of 93, and stale since 2026-09-06. It then listed the failure
kinds and recommended, as its own item 4, that the 63 "syntax only" functions be triaged because
they are the largest clean bucket and the least understood.

This is that triage, plus item 3 (split `no text symbols`). Both are deterministic, LLM-free and
read-only.

THE DISTINCTION THAT MATTERS. A function whose stored source contains no C at all did not fail
because the model wrote bad C -- it failed because the pipeline handed the compiler something that
was never C. Those have opposite fixes:

    PIPELINE   the extraction/fencing path. Fix it once and every affected function is re-admitted
               with no model calls.
    MODEL      the model wrote C and the C is wrong. Fix it with prompt constraints or more samples.

`eval/trajectory_factory.py` already produced one measured instance of the PIPELINE case: 19,791
characters of reasoning handed to the compiler, reported as "Unterminated string or character
constant". A single instance is an anecdote; the question is how much of the 199 it accounts for.

PRE-REGISTRATION (before running).

Population: functions with >=1 attempt and 0 compiling attempts (the 199).
Unit of classification: the ATTEMPT's stored `source_code`, which is exactly what the pipeline
handed the compiler.
Classes, decided by inspecting that text, never by reading the error message alone (the project's
own rule: `Syntax Error` is half of everything and names no cause):
    EMPTY          whitespace only
    NO_FUNCTION    no `name(...) {` shape anywhere
    PROSE          markdown fences, headings, or a prose preamble before any code
    REFUSAL        the model declined
    TRUNCATED      an unclosed block comment or unbalanced braces
    C              otherwise: this is C the model wrote, and the compiler rejected it

Function-level verdict: UNANIMOUS when every attempt lands in one class, else MIXED.

Pre-registered hypotheses:
    H1  A non-trivial share of the 199 is PIPELINE, not MODEL. If it is under 10% the bucket is a
        model problem and the fix is generation-side.
    H0  PIPELINE is negligible. Then say so plainly and stop looking here.

Guards:
    - Anything that fires on zero functions is a FINDING, not a shrug (the fifth rule).
    - libultra translation units are excluded from the accounting, using `eval.clean_set.EXCLUDE_TU`
      -- three separate threads have now been bitten by counting them.
    - Read-only. `mode=ro` on the KB, and this module writes nothing but its own report.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solver import llm  # noqa: E402
from eval.clean_set import EXCLUDE_TU  # noqa: E402

DB = "file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro"

FENCE = re.compile(r"```")
HEADING = re.compile(r"(?m)^\s*#{1,6}\s+\S")
PROSE_HEAD = re.compile(r"^\s*(We |I |Let |Here|Sure|The |This |To |First|Okay|Note that|"
                        r"Alright|So )")
PROSE_TELL = re.compile(r"\b(we need to|let me|I'll|I will|the assembly|we can|we should)\b", re.I)
NO_TEXT_SYMBOLS = "no text symbols"


def classify_source(code: str | None) -> str:
    """What the pipeline actually handed the compiler. Never inferred from the error message."""
    text = (code or "").strip()
    if not text:
        return "EMPTY"
    if llm.is_refusal(text):
        return "REFUSAL"
    head = text[:400]
    if FENCE.search(text) or HEADING.search(head) or PROSE_HEAD.match(head):
        # A fence alone is not prose -- a well-formed answer is one fenced block. Prose is a fence
        # PLUS a preamble, or a heading, or a sentence where a declaration should be.
        if PROSE_HEAD.match(head) or HEADING.search(head) or (FENCE.search(text)
                                                              and PROSE_TELL.search(head)):
            return "PROSE"
    if text.count("/*") > text.count("*/"):
        return "TRUNCATED"
    if text.count("{") != text.count("}"):
        return "TRUNCATED"
    if not llm.FUNC_DEF_RE.search(text):
        return "NO_FUNCTION"
    return "C"


def first_error_kind(stderr: str | None) -> str:
    """A coarse bucket for the compiler's own words, used to sub-classify real C failures."""
    text = (stderr or "").lower()
    if not text.strip():
        return "no stderr"
    if "do-while" in text:
        return "do-while ban"
    if NO_TEXT_SYMBOLS in text:
        return "no text symbols"
    if "undefined" in text:
        return "undefined identifier"
    if "redeclaration" in text or "redefined" in text:
        return "redeclaration"
    if "selector requires" in text:
        return "selector requires struct/union"
    if "syntax error" in text or "illegal" in text or "parse" in text:
        return "syntax error"
    return "other"


def _library_filter(alias: str) -> str:
    return " ".join(f"and {alias}.name not like '{pattern}'" for pattern in EXCLUDE_TU)


def connect(db: str = DB) -> sqlite3.Connection:
    return sqlite3.connect(db, uri=True)


def never_compiled(conn: sqlite3.Connection) -> list[dict]:
    """The 199, with libultra removed from the accounting."""
    rows = conn.execute(
        "select f.addr, f.name, t.name from functions f join tus t on t.id = f.tu_id "
        "where exists (select 1 from attempts a where a.func_addr = f.addr) "
        "and not exists (select 1 from attempts a where a.func_addr = f.addr and a.compiled = 1) "
        + _library_filter("t")).fetchall()
    return [{"addr": addr, "name": name, "tu": tu} for addr, name, tu in rows]


def triage(conn: sqlite3.Connection, population: list[dict]) -> dict:
    per_function: dict[str, dict] = {}
    for item in population:
        rows = conn.execute(
            "select id, source_code, compiler_stderr, done_reason, extract_status, model "
            "from attempts where func_addr = ? order by id", (item["addr"],)).fetchall()
        kinds = collections.Counter()
        errors = collections.Counter()
        examples: dict[str, int] = {}
        for rid, code, stderr, done, status, model in rows:
            kind = classify_source(code)
            kinds[kind] += 1
            examples.setdefault(kind, rid)
            if kind == "C":
                errors[first_error_kind(stderr)] += 1
        verdict = "UNANIMOUS" if len(kinds) == 1 else "MIXED"
        dominant = kinds.most_common(1)[0][0] if kinds else "EMPTY"
        per_function[item["name"]] = {
            "attempts": len(rows), "kinds": dict(kinds), "verdict": verdict,
            "dominant": dominant, "errors": dict(errors), "examples": examples,
            "done_reasons": dict(collections.Counter(str(row[3]) for row in rows)),
            "extract_status": dict(collections.Counter(str(row[4]) for row in rows)),
            "models": dict(collections.Counter(str(row[5]) for row in rows)),
        }
    return per_function


def report(per_function: dict, population: int) -> dict:
    kind_totals = collections.Counter()
    verdict_totals = collections.Counter()
    dominant_totals = collections.Counter()
    error_totals = collections.Counter()
    for row in per_function.values():
        verdict_totals[row["verdict"]] += 1
        dominant_totals[row["dominant"]] += 1
        for kind, count in row["kinds"].items():
            kind_totals[kind] += count
        for kind, count in row["errors"].items():
            error_totals[kind] += count
    pipeline = sum(1 for r in per_function.values()
                   if r["dominant"] in ("EMPTY", "NO_FUNCTION", "PROSE", "REFUSAL", "TRUNCATED"))
    model = sum(1 for r in per_function.values() if r["dominant"] == "C")
    return {"functions": population, "function_classes": dict(dominant_totals),
            "function_verdicts": dict(verdict_totals),
            "attempt_classes": dict(kind_totals), "compiler_error_kinds": dict(error_totals),
            "pipeline_dominant": pipeline, "model_dominant": model,
            "pipeline_share": round(pipeline / max(1, population), 4)}


def no_text_symbols_breakdown(conn: sqlite3.Connection) -> dict:
    """`no text symbols` covers three different things wearing one message.

    `noopThreeArgs`/`noopFourArgs` are genuine no-ops: IDO emits nothing because the C does nothing,
    so the target bytes may not be producible from C at all -- the `bootThreadMain` class. libultra
    should never have been attempted. Counting either as a model failure corrupts every aggregate
    built on top of it.
    """
    rows = conn.execute(
        "select distinct f.addr, f.name, t.name, "
        " (select a.source_code from attempts a where a.func_addr = f.addr order by a.id desc "
        "  limit 1) "
        "from functions f join tus t on t.id = f.tu_id "
        "where exists (select 1 from attempts a where a.func_addr = f.addr and a.compiled = 0 "
        "              and a.compiler_stderr like '%no text symbols%')").fetchall()
    library = tuple(EXCLUDE_TU)
    out = {"noop": [], "library": [], "real": []}
    for addr, name, tu, source in rows:
        if any(pattern.strip("%").replace("%", "") in tu for pattern in library):
            out["library"].append(name)
        elif re.match(r"noop|^_?noop", name) or _body_is_empty(source):
            out["noop"].append(name)
        else:
            out["real"].append(name)
    return {key: {"count": len(value), "names": sorted(value)[:20]}
            for key, value in out.items()}


def _body_is_empty(source: str | None) -> bool:
    """True when the last stored draft has a function body containing no statements."""
    text = source or ""
    if "{" not in text or "}" not in text:
        return False
    inner = text[text.index("{") + 1:text.rindex("}")]
    stripped = re.sub(r"/\*.*?\*/", "", inner, flags=re.DOTALL)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    return not stripped.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", default=DB)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    conn = connect(args.kb)
    population = never_compiled(conn)
    per_function = triage(conn, population)
    payload = {
        "population_excluding_libultra": len(population),
        "triage": report(per_function, len(population)),
        "no_text_symbols": no_text_symbols_breakdown(conn),
        "functions": per_function,
    }
    text = json.dumps(payload, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    summary = payload["triage"]
    print(json.dumps({k: v for k, v in summary.items()}, indent=2))
    print(json.dumps(payload["no_text_symbols"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
