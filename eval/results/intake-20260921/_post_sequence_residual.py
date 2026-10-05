"""The residual AFTER the sequence, measured on the candidate the sequence actually produces.

WHY THE PREVIOUS CHAIN ANALYSIS WAS WRONG, and it is the same mistake the whole session has been finding:

    SEQUENCE = resolve_placeholders, frontend_diagnostics, negative_offset, header_variant, globals_variant,
               opaque_variant, rewrite_do_while

The frontend runs at position 2. `header_variant` runs at position 4 and adds headers for names the
frontend reported undeclared -- measured: `renderRacePickupRespawn` goes from 11 undeclared identifiers to
ZERO across that step. So "undeclared-identifier is the whole distance for 22 states" was a true statement
about a candidate that no longer exists by the end of the sequence. It is cfe's-first-error disease with a
different instrument: the diagnostic was read at the wrong point in the pipeline.

This re-derives each state through the whole sequence, runs the frontend on the FINAL candidate, and
classifies what is ACTUALLY left. Read-only apart from the attempts the oracle logs.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from eval.intake_probe import SEQUENCE, gated, rank                        # noqa: E402
from eval.intake_runners import RUNNERS                                    # noqa: E402
from eval.tool_agent_run import build_context                              # noqa: E402
from solver import frontend_diagnostics as frontend                        # noqa: E402

REPO = Path.home() / "decomp/sbk1"
KB = Path.home() / "decomp/kb-sbk1.sqlite"
BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")

CLASSES = (
    ("member-on-typed-pointer", re.compile(r"member reference base type|not a structure or union")),
    ("undeclared-member", re.compile(r"no member named|has no member")),
    ("unknown-type-name", re.compile(r"unknown type name")),
    ("undeclared-identifier", re.compile(r"use of undeclared identifier|undeclared identifier")),
    ("undeclared-function", re.compile(r"implicit declaration of function")),
    ("redeclaration/conflict", re.compile(r"redeclaration|conflicting types|previous declaration")),
    ("incompatible-pointer", re.compile(r"incompatible pointer types")),
    ("parameter-declarator", re.compile(r"expected parameter declarator|type name requires a specifier")),
    ("expected-identifier", re.compile(r"expected identifier")),
    ("other-syntax", re.compile(r"syntax|expected|invalid|illegal", re.I)),
)


def classify(message: str) -> str:
    for label, pattern in CLASSES:
        if pattern.search(message):
            return label
    return "unclassified"


frame = json.loads((BASE / "wide-frame.json").read_text(encoding="utf-8"))["rows"]
conn = sqlite3.connect(str(KB))
rows = []
try:
    for index, entry in enumerate(frame, 1):
        name = entry["function"]
        context, why = build_context(REPO, name, conn=conn)
        if context is None:
            continue
        initial = dict(context.initial_verdict or {})
        current, best, stderr = context.candidate, initial, initial.get("stderr") or ""
        for label in SEQUENCE:
            if not gated(label, stderr):
                continue
            ns = {**context.__dict__, "candidate": current, "kb_conn": conn,
                  "initial_verdict": {**initial, "stderr": stderr}}
            try:
                result = RUNNERS[label](ns, {})
            except Exception:                                          # noqa: BLE001
                continue
            if not result.get("changed"):
                continue
            verdict = context.compile_fn(result["source"])
            if rank(verdict) >= rank(best):
                current, best = result["source"], verdict
                stderr = verdict.get("stderr") or ""
        row = {"function": name, "tier": entry.get("tier"),
               "compiled": bool(best.get("compiled")), "exact": bool(best.get("exact"))}
        if not row["compiled"]:
            report = frontend.analyse(current, repo=REPO, target=str(context.target or ""))
            classes = []
            for error in (report.get("errors") or []):
                found = classify(error.get("what") or "")
                if found not in classes:
                    classes.append(found)
            row.update(status=report.get("status"), error_count=report.get("error_count", 0),
                       classes=classes)
        rows.append(row)
        if index % 40 == 0:
            print(f"  ... {index}/{len(frame)}", flush=True)
finally:
    conn.close()

blocked = [r for r in rows if not r["compiled"]]
converted = [r for r in rows if r["compiled"]]
print(f"\nstates {len(rows)}   compiled {len(converted)}   blocked {len(blocked)}")

print("\nCHAIN LENGTH after the whole sequence:")
lengths = Counter(len(r.get("classes") or []) for r in blocked)
for length in sorted(lengths):
    print(f"  {length:2} class(es): {lengths[length]:4} states")
sizes = [len(r.get("classes") or []) for r in blocked]
if sizes:
    print(f"  mean {sum(sizes) / len(sizes):.2f}, median {sorted(sizes)[len(sizes) // 2]}")

single = [r for r in blocked if len(r.get("classes") or []) == 1]
print(f"\nSINGLE-CLASS states: {len(single)} of {len(blocked)}")
for row in single[:30]:
    print(f"    {row['function']:44} {row.get('tier'):7} {row['classes'][0]}")

payoff: dict[str, dict] = defaultdict(lambda: {"whole": 0, "present": 0})
for row in blocked:
    for name in (row.get("classes") or []):
        payoff[name]["present"] += 1
    if len(row.get("classes") or []) == 1:
        payoff[row["classes"][0]]["whole"] += 1
print(f"\nPER-CLASS PAYOFF, on the FINAL candidate this time:")
print(f"  {'class':26} {'whole distance':>14} {'in the way':>11}")
for name in sorted(payoff, key=lambda n: -payoff[n]["whole"]):
    print(f"  {name:26} {payoff[name]['whole']:14} {payoff[name]['present']:11}")

(BASE / "post-sequence-residual.json").write_text(json.dumps(
    {"states": len(rows), "compiled": len(converted), "blocked": len(blocked),
     "chain_lengths": {str(k): v for k, v in sorted(lengths.items())},
     "mean_chain": (sum(sizes) / len(sizes)) if sizes else None,
     "single_class": [{"function": r["function"], "tier": r.get("tier"), "class": r["classes"][0]}
                      for r in single],
     "payoff": {k: v for k, v in payoff.items()},
     "rows": rows,
     "note": ("diagnostics taken on the candidate the full sequence produces, unlike chain-analysis.json "
              "whose reading predated header_variant")},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'post-sequence-residual.json'}")
