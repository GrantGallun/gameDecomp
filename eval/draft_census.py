"""How far is m2c's assembly-only draft from a correct source's shape, and does that predict what the pipeline solves?

    python -m eval.draft_census --workers 8

For every function with a target: an assembly-only m2c draft (solver.m2c_input.draft, no context) and the reference
decomp's body, compared on shape features: control-flow constructs, logical operators, statements, locals, calls.
The reference is a GRADER here: it measures a tool (m2c) and is never written to a ledger, draft, prompt or ranker
(CLAUDE.md, "ground truth is for checking"). The output is per-function rows plus a summary split by whether the
pipeline has an exact of its own (recovered or reference-copied exacts excluded).
"""
from __future__ import annotations

import argparse
import collections
import json
import multiprocessing
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REPO = Path("/home/grant/decomp/sbk1")
OUT = ROOT / "eval" / "results" / "draft-census-20260930"
LEDGERS = (Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"),
           Path("/home/grant/decomp/kb-sbk1.sqlite"))
STATE = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json")
EXCLUDED = ("recover", "history", "provenance", "reference", "relocated-oracle", "retrodiction")

FEATURES = {
    "if": r"\bif\s*\(", "else": r"\belse\b", "for": r"\bfor\s*\(", "while": r"\bwhile\s*\(", "do": r"\bdo\b",
    "switch": r"\bswitch\s*\(", "case": r"\bcase\b", "goto": r"\bgoto\b", "label": r"^\s*[A-Za-z_]\w*:(?!:)",
    "return": r"\breturn\b", "and": r"&&", "or": r"\|\|", "ternary": r"\?", "break": r"\bbreak\s*;",
    "continue": r"\bcontinue\s*;",
}
CONTROL = ("if", "else", "for", "while", "do", "switch", "goto", "and", "or", "ternary", "return")
LOOPS = ("for", "while", "do")


def shape(body: str) -> dict:
    from solver import c89
    masked = c89._mask(body)
    # m2c spells an unknown type `?` (`? arg1`, `extern ? sym;`): not a ternary
    masked = re.sub(r"(^|[(,;{]|\bextern)(\s*)\?(\s+)(?=[A-Za-z_])", r"\1\2U\3", masked, flags=re.M)
    out = {k: len(re.findall(p, masked, re.M)) for k, p in FEATURES.items()}
    out["statements"] = masked.count(";") - 2 * out["for"]
    out["calls"] = len(re.findall(r"\b(?!if|for|while|switch|return|sizeof)[A-Za-z_]\w*\s*\(", masked))
    out["locals"] = len(re.findall(
        r"^\s*(?:(?:unsigned|signed|const|volatile|struct|union|register)\s+)*"
        r"(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|char|short|long|float|double|void|[A-Z]\w*)\b[\s*]+\w+\s*[;=\[]",
        masked, re.M))
    out["depth"] = _depth(masked)
    return out


def _depth(masked: str) -> int:
    d = best = 0
    for ch in masked:
        if ch == "{":
            d += 1
            best = max(best, d)
        elif ch == "}":
            d -= 1
    return best - 1


def _body(source: str, name: str) -> str | None:
    from solver import repair_context
    try:
        match, end = repair_context.definition(source, name)
    except Exception:
        return None
    return source[match.start():end]


def reference_index() -> dict:
    """function name -> reference source file (grader only)."""
    out = {}
    for path in (REPO / "src").rglob("*.c"):
        text = path.read_text(errors="replace")
        for m in re.finditer(r"(?m)^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*\{", text):
            out.setdefault(m.group(1), str(path))
    return out


def solved_by_pipeline() -> set:
    names = set()
    for path in LEDGERS:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for name, strategy in db.execute("select f.name, a.strategy from attempts a join functions f on "
                                         "f.addr=a.func_addr where a.exact=1"):
            if not any(x in (strategy or "") for x in EXCLUDED):
                names.add(name)
        db.close()
    try:
        from eval import campaign_state
        state = campaign_state.read(STATE)
        names |= {n for n, v in state["nodes"].items()
                  if v.get("status") in ("integrated", "function_exact_pending_integration")}
    except Exception:
        pass
    return names


def one(item):
    from solver import m2c_input
    name, ref_path = item
    row = {"function": name}
    target = REPO / "nonmatchings" / name / "target.s"
    try:
        result, meta = m2c_input.draft(REPO, target)
    except Exception as exc:
        row["error"] = repr(exc)[:200]
        return row
    if result.returncode != 0:
        row["error"] = "m2c failed: " + (result.stderr or "")[-160:]
        return row
    draft = result.stdout
    row["draft_failure"] = "Decompilation failure" in draft or "M2C_ERROR" in draft
    body = _body(draft, name)
    ref = _body(Path(ref_path).read_text(errors="replace"), name)
    if body is None or ref is None:
        row["error"] = "body not found in " + ("draft" if body is None else "reference")
        return row
    row["draft"], row["reference"] = shape(body), shape(ref)
    row["insns"] = sum(1 for line in target.read_text().splitlines() if re.match(r"\s*/\*\s*[0-9A-F]+ ", line))
    return row


def summarise(rows, solved):
    ok = [r for r in rows if "draft" in r]

    def control_equal(r, keys=CONTROL):
        return all(r["draft"][k] == r["reference"][k] for k in keys)

    def loops(s):
        return sum(s[k] for k in LOOPS)

    groups = {"solved": [r for r in ok if r["function"] in solved], "unsolved": [r for r in ok if r["function"] not in solved]}
    out = {"functions": len(rows), "compared": len(ok),
           "errors": collections.Counter(r["error"][:40] for r in rows if "error" in r).most_common(8)}
    for g, rs in groups.items():
        n = max(1, len(rs))
        feat = {}
        for k in list(FEATURES) + ["statements", "calls", "locals", "depth"]:
            d = [r["draft"][k] - r["reference"][k] for r in rs]
            feat[k] = {"differs": round(sum(x != 0 for x in d) / n, 3), "draft_more": round(sum(x > 0 for x in d) / n, 3),
                       "mean_delta": round(sum(d) / n, 2)}
        out[g] = {"n": len(rs),
                  "control_shape_equal": round(sum(control_equal(r) for r in rs) / n, 3),
                  "branches_equal": round(sum(control_equal(r, ("if", "else", "and", "or", "ternary")) for r in rs) / n, 3),
                  "loop_count_equal": round(sum(loops(r["draft"]) == loops(r["reference"]) for r in rs) / n, 3),
                  "goto_in_draft": round(sum(r["draft"]["goto"] > 0 for r in rs) / n, 3),
                  "goto_in_reference": round(sum(r["reference"]["goto"] > 0 for r in rs) / n, 3),
                  "draft_failure": round(sum(bool(r.get("draft_failure")) for r in rs) / n, 3),
                  "features": feat}
    # size-stratified: solved and unsolved differ in size, so a pooled ratio would mostly measure size
    bands = ((0, 30), (30, 80), (80, 200), (200, 10**6))
    out["by_size"] = {}
    for lo, hi in bands:
        row = {}
        for g, rs in groups.items():
            b = [r for r in rs if lo <= r.get("insns", 0) < hi]
            row[g] = {"n": len(b), "control_shape_equal": round(sum(control_equal(r) for r in b) / max(1, len(b)), 3)}
        out["by_size"][f"{lo}-{hi}"] = row
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    index = reference_index()
    names = sorted(p.parent.name for p in (REPO / "nonmatchings").glob("*/target.s") if p.parent.name in index)
    if args.limit:
        names = names[:args.limit]
    path = OUT / "rows.jsonl"
    done = {json.loads(l)["function"] for l in path.read_text().splitlines()} if path.exists() else set()
    with multiprocessing.Pool(args.workers) as pool, path.open("a") as fh:
        for row in pool.imap_unordered(one, [(n, index[n]) for n in names if n not in done], chunksize=4):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    summary = summarise(rows, solved_by_pipeline())
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k not in ("solved", "unsolved")}, indent=1))
    for g in ("solved", "unsolved"):
        s = summary[g]
        print(g, {k: v for k, v in s.items() if k != "features"})


if __name__ == "__main__":
    main()
