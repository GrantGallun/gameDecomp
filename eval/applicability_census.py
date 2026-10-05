"""No-compile applicability census: how often does each generator fire on the dev near-misses?

Run BEFORE building or judging a generator. A generator that fires on <5% of near-misses cannot be judged by
the sealed instrument (see eval/seal.py power table) and should be catalogued, not built. DEV side only:
``seal.assert_dev_only`` guards the input. Deterministic, no compiles, no model.

    python3 -m eval.applicability_census --manifest eval/sets/sbk1_v5_sealed_nearmiss.json --out census.json

Levels: L2 = the generator emits >=1 source that differs from the pinned start. (L0/L1, whether the target
assembly contains the shape and whether the residual touches it, need target dumps; not computed here.)
A generator that raises is counted as an error and reported, never as a non-fire.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import seal  # noqa: E402

LEDGERS = {"kb": "/home/grant/decomp/kb-sbk1.sqlite",
           "campaign": "/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite"}
# CALLS: name -> (module, function, takes_diff)
GENERATORS = {
    "narrow_update": ("solver.narrow_update", "variants", False),
    "scalar_coalesce": ("solver.scalar_coalesce", "variants", False),
    "scoped_field": ("solver.scoped_field", "variants", False),
    "counted_loop": ("solver.counted_loop", "variants", True),
    "branch_shape": ("solver.branch_shape", "variants", True),      # also takes evidence (compiler recipe)
    "temp_copyback": ("solver.temp_copyback", "variants", True),
    "unaligned_copy": ("solver.unaligned_copy", "variants", True),
    "regalloc_mutations": ("solver.regalloc_mutations", "variants", True),
}
# Spelling-independent 'meaning present' for the narrow store/reread shape (for guard attribution, H1)
TAKES_EVIDENCE = {"branch_shape"}
MEANING = re.compile(r"(->\s*\w+\s*(\+\+|\+=\s*1\b))|(\+\+\s*\w+\s*->\s*\w+)|(\*\s*\([^)]*\*\)[^;]*\+\s*1\b)")


def _texts(variants) -> list[str]:
    out = []
    for v in variants:
        out.append(v if isinstance(v, str) else (v[0] if isinstance(v, (tuple, list)) and isinstance(v[0], str)
                                                 else getattr(v, "source", getattr(v, "code", ""))))
    return out


def census(rows: list[dict], ledgers: dict[str, sqlite3.Connection]) -> dict:
    import importlib
    mods = {name: getattr(importlib.import_module(m), fn) for name, (m, fn, _) in GENERATORS.items()}
    result = {name: {"fires": [], "errors": {}} for name in GENERATORS}
    meaning, per_function, missing_evidence = [], {}, []
    for row in rows:
        start = row["start"]
        source, diff, sampling = ledgers[start["ledger"]].execute(
            "SELECT source_code, diff_summary, sampling FROM attempts WHERE id=?", (start["attempt_id"],)).fetchone()
        try:
            recorded = json.loads(sampling or "{}")
        except ValueError:
            recorded = {}
        evidence = {k: recorded[k] for k in ("compiler_recipe", "frontend", "source_attribution") if k in recorded}
        if "compiler_recipe" not in evidence:
            missing_evidence.append(row["function"])       # a decline here may be missing evidence, not a non-fire
        name = row["function"]
        per_function[name] = []
        if MEANING.search(source or ""):
            meaning.append(name)
        for gen, fn in mods.items():
            try:
                if gen in TAKES_EVIDENCE:
                    produced = _texts(fn(source, name, diff or "", evidence))
                else:
                    produced = _texts(fn(source, name, diff or "") if GENERATORS[gen][2] else fn(source, name))
            except Exception as exc:                       # noqa: BLE001 - an error is not a non-fire
                result[gen]["errors"][name] = f"{type(exc).__name__}: {exc}"[:160]
                continue
            if any(t and t != source for t in produced):
                result[gen]["fires"].append(name)
                per_function[name].append(gen)
    tus = {r["function"]: r["tu"] for r in rows}
    n = len(rows)
    summary = {}
    for gen, data in result.items():
        fired = data["fires"]
        summary[gen] = {"fires": len(fired), "rate": round(len(fired) / n, 3),
                        "tus": len({tus[f] for f in fired}), "errors": len(data["errors"]),
                        "decision": ("build/judge" if len(fired) / n >= 0.10 and len({tus[f] for f in fired}) >= 5
                                     else "bundle-only" if len(fired) / n >= 0.05 else "catalog-only")}
    return {"functions": n, "starts_without_recorded_compiler_recipe": len(missing_evidence),
            "meaning_present_any_spelling": len(meaning), "meaning_present_names": meaning,
            "generators": summary, "errors": {g: d["errors"] for g, d in result.items() if d["errors"]},
            "any_generator_fires": sum(bool(v) for v in per_function.values())}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    rows = manifest["dev"]
    seal.assert_dev_only([r["function"] for r in rows], manifest)      # development reads dev only
    ledgers = {k: sqlite3.connect(f"file:{v}?mode=ro", uri=True) for k, v in LEDGERS.items()}
    report = census(rows, ledgers)
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("functions", "starts_without_recorded_compiler_recipe",
                                             "meaning_present_any_spelling", "generators", "any_generator_fires")}, indent=1))
    if report["errors"]:
        print("ERRORS:", json.dumps({g: len(e) for g, e in report["errors"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
