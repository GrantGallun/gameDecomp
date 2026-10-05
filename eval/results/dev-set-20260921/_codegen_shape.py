"""What the remaining distance actually is, per function, in the oracle's own words.

Phase B's search showed the catalog is exhausted at the finish line: 14 of 17 finished candidates admit no
move at all, and the one action that could still fire was destroying them. That leaves one question worth
answering before writing anything new: WHAT is the residual? This prints, for the highest-scoring panel
members:

  * the oracle's similarity and the instruction delta,
  * the fault classes from `solver.signals.analyse` -- the project's supported classifier, not a new one,
  * the target-only and candidate-only instruction lines, so the shape is visible rather than summarised.

Run in WSL from the repo root:
  PYTHONPATH=/mnt/c/Code/gameDecomp python -m eval.results.dev-set-20260921._codegen_shape
"""
from __future__ import annotations

import difflib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval.tool_agent_run import build_context                              # noqa: E402
from solver import signals                                                 # noqa: E402

FUNCTIONS = ("__MusIntProcessWobble", "updateRacePlayerAirborneLaunch", "osEPiRawWriteIo",
             "updateRacePlayerMode06TerrainFall", "updateRacePlayerAirborneLaunch")
SOURCES = ROOT / "eval/results/dev-set-20260921/sources"
REPO = Path.home() / "decomp/sbk1"
OUT = ROOT / "eval/results/dev-set-20260921/codegen-shape.json"


def interesting(line: str) -> str:
    """The instruction and its operands, without the address columns and comments."""
    text = line.split("#", 1)[0].rstrip()
    parts = text.split("\t")
    return "\t".join(part for part in parts if part).strip()


report = {}
for name in dict.fromkeys(FUNCTIONS):
    source = (SOURCES / f"{name}.c").read_text(encoding="utf-8")
    context, why = build_context(REPO, name)
    if context is None:
        print(f"{name}: no context ({why})")
        continue
    verdict = context.compile_fn(source)
    diff = verdict.get("diff") or ""
    classified = signals.analyse(diff, score=float(verdict.get("score") or 0.0),
                                exact=bool(verdict.get("exact")),
                                compiled=bool(verdict.get("compiled")))
    target = (context.target_dump or "").splitlines()
    candidate = (verdict.get("dump") or "").splitlines()
    matcher = difflib.SequenceMatcher(a=[interesting(l) for l in target],
                                      b=[interesting(l) for l in candidate], autojunk=False)
    only_target, only_candidate = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            only_target.extend([l.strip() for l in target[i1:i2] if l.strip()])
        if tag in ("replace", "insert"):
            only_candidate.extend([l.strip() for l in candidate[j1:j2] if l.strip()])
    opcodes = Counter()
    for line in only_target + only_candidate:
        word = interesting(line).split(" ")[0].split("\t")[0]
        if word:
            opcodes[word] += 1
    entry = {"score": verdict.get("score"), "compiled": verdict.get("compiled"),
             "exact": verdict.get("exact"),
             "instr_delta": classified.instr_delta,
             "faults": {kind: getattr(classified, kind) for kind in
                        ("layout", "offset", "width", "structural", "reloc", "regalloc", "ordering",
                         "immediate")},
             "target_lines": len([l for l in target if l.strip()]),
             "candidate_lines": len([l for l in candidate if l.strip()]),
             "differing_target_lines": len(only_target),
             "differing_candidate_lines": len(only_candidate),
             "opcode_histogram_of_the_difference": dict(opcodes.most_common(12)),
             "first_target_only": only_target[:8],
             "first_candidate_only": only_candidate[:8]}
    report[name] = entry
    print("=" * 78)
    print(f"{name}   score={entry['score']}  instr_delta={entry['instr_delta']}")
    print(f"  faults: {json.dumps(entry['faults'])}")
    print(f"  target {entry['target_lines']} lines, candidate {entry['candidate_lines']} lines, "
          f"{entry['differing_target_lines']} vs {entry['differing_candidate_lines']} differ")
    print(f"  opcodes in the difference: {json.dumps(entry['opcode_histogram_of_the_difference'])}")
    for label, lines in (("TARGET ONLY", only_target), ("CANDIDATE ONLY", only_candidate)):
        print(f"  {label}:")
        for line in lines[:8]:
            print(f"    {line}")

OUT.write_text(json.dumps({"schema_version": 1, "kind": "codegen-shape",
                           "note": ("Read off the oracle's own normalized dumps and classified with "
                                    "solver.signals. The panel is development data."),
                           "functions": report}, indent=2) + "\n", encoding="utf-8")
print(f"\nwritten {OUT}")
