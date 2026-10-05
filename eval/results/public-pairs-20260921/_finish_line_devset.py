"""Build a panel from the ONLY functions that are genuinely one operator away.

CORRECTED COUNT, and the correction is the point. The KB reports 218 functions that compile and are not
exact. Of those, **211 are implemented in the reference decomp's `src/**`** -- already solved -- and 7 are
not. `functions.state` cannot separate them: it says `matched` for all 2,113 rows, so it is an inventory
column, not a solve indicator, and keying on it would have excluded 218 of 218 for the wrong reason while
looking exactly like a clean guard.

The 7, with the KB's best score:

    __osPopThread              95.000
    osEPiRawWriteIo            91.474   (already in the frozen intake panel)
    osEPiRawReadIo             79.150   (already in the frozen intake panel)
    __sinf                     63.297
    __cosf                     54.593
    osPiRawStartDma            50.643
    drawMenuAsciiFontTile      11.399

This is the whole remaining "finish line" population of the target. It is not a vein; it is seven
functions, which is worth knowing before planning around it.

Run in WSL:
  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.public-pairs-20260921._finish_line_devset
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path("/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
KB = Path.home() / "decomp/kb-sbk1.sqlite"
REPO = Path.home() / "decomp/sbk1"
NAMES = ("__osPopThread", "osEPiRawWriteIo", "osEPiRawReadIo", "__sinf", "__cosf", "osPiRawStartDma",
         "drawMenuAsciiFontTile")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    from eval.tool_agent_run import build_context

    sources = HERE / "finish-line-sources"
    sources.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
    entries, problems = [], []
    try:
        for name in NAMES:
            row = conn.execute(
                "select a.func_addr, a.source_code, a.score, a.source_sha256 from attempts a "
                "join functions f on f.addr = a.func_addr where f.name = ? "
                "and coalesce(a.compiled,0)=1 and a.source_code is not null "
                "order by a.score desc, a.id desc limit 1", (name,)).fetchone()
            if not row:
                problems.append({"function": name, "reason": "no compiling attempt in the KB"})
                continue
            addr, source, score, recorded = row
            if sha(source) != (recorded or sha(source)):
                problems.append({"function": name, "reason": "the recorded source hash does not match"})
                continue
            context, why = build_context(REPO, name)
            if context is None:
                problems.append({"function": name, "reason": f"no context: {why}"})
                continue
            (sources / f"{name}.c").write_text(source, encoding="utf-8")
            verdict = context.compile_fn(source)
            if not verdict.get("compiled"):
                problems.append({"function": name, "reason": "the KB candidate no longer compiles"})
                continue
            if verdict.get("exact"):
                problems.append({"function": name, "reason": "ALREADY EXACT -- exclude, not a target"})
                continue
            entries.append({"function": name, "sha256": sha(source),
                            "baseline_draft_sha256": sha(context.candidate or ""),
                            "assistance": {"tier": "binary-only",
                                           "note": "no declaration assistance is claimed for the KB's "
                                                   "own attempt"},
                            "recorded": {"ido_compiled": True, "exact": False, "kb_best_score": score,
                                         "fresh_score": verdict.get("score"), "size": None,
                                         "kb_state": "not in src/**"},
                            "lineage": {"source": "kb best-scoring compiled attempt",
                                        "func_addr": addr},
                            "training_eligible": False,
                            "training_exclusion": "development panel: measured on, never trained on"})
            print(json.dumps({"function": name, "kb_best": score, "fresh": verdict.get("score")}),
                  flush=True)
    finally:
        conn.close()
    payload = {"schema_version": 5, "kind": "finish-line-devset", "kb": str(KB),
               "selection": {"selected": len(entries), "problems": problems,
                             "note": ("the complete set of target-game functions that compile, are not "
                                      "exact, and are not already implemented in src/**")},
               "regime": ("REAL TARGET near misses. Exposed development data; never a sealed evaluation "
                          "and never training data."),
               "entries": entries}
    out = HERE / "finish-line-devset.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": len(entries), "problems": problems}, indent=2))
    print("written", out)
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
