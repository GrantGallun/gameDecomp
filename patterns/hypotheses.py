"""Hypothesis bank: every idea we have tested, and what happened.

The point is to never pay for the same experiment twice. A refuted hypothesis
is as valuable as a confirmed one and far more perishable -- nobody writes down
"we tried this and it did nothing", so it gets re-tried forever. The reference
decomp's own commits spend most of their words on what was searched and found
inert, which is exactly why that repo is worth mining.

Statuses:

    CONFIRMED     tested, works, and is allowed to change behaviour
    REFUTED       tested, does not work. Do not re-try without NEW evidence.
    INCONCLUSIVE  tested, result inside noise. Needs more power, not more faith.
    UNTESTED      queued from a source, not yet measured. May NOT steer the
                  solver -- a hypothesis does not get to change behaviour.

Query before implementing:
    python3 -m patterns.hypotheses --check "sequential refinement"
    python3 -m patterns.hypotheses --status REFUTED
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

BANK = Path(__file__).parent / "hypotheses.json"


@dataclass
class Hypothesis:
    id: str
    claim: str
    source: str
    status: str                      # CONFIRMED | REFUTED | INCONCLUSIVE | UNTESTED
    evidence: str = ""
    tested_on: str = ""              # what set / how many functions
    date: str = ""
    keywords: list = field(default_factory=list)


SEED: list[Hypothesis] = [
    Hypothesis(
        id="sequential-diff-refinement",
        claim="Feeding the instruction diff back to the model produces a "
              "targeted fix and converges on a match.",
        source="own design intuition",
        status="REFUTED",
        evidence="Across 8 functions it rescued ZERO. Every match came on the "
                 "first attempt. Best-of-N on the same budget got 6/8 vs 3/8. "
                 "The model cannot invert 'this instruction differs' into "
                 "'this source change fixes it'; shown a diff it rewrites "
                 "plausible-but-different C rather than converging.",
        tested_on="8 leaf functions, gpt-oss:20b, budget-matched A/B",
        date="2026-08-27",
        keywords=["refine", "diff", "feedback", "iterate", "sequential"],
    ),
    Hypothesis(
        id="best-of-n-sampling",
        claim="Independent best-of-N sampling beats sequential refinement at "
              "equal budget, because the answer is already in the model's "
              "distribution.",
        source="TRAINING.md Tier 1, then measured",
        status="CONFIRMED",
        evidence="6/8 vs 3/8 exact at identical budget. Direct proof of the "
                 "mechanism: the same function resampled from an identical "
                 "prompt scored 87.61%, 82.42%, then 100%.",
        tested_on="8 leaf functions, gpt-oss:20b",
        date="2026-08-27",
        keywords=["sampling", "best-of-n", "temperature", "draws"],
    ),
    Hypothesis(
        id="permute-below-95",
        claim="decomp-permuter can close a near-miss below 95%.",
        source="own assumption",
        status="REFUTED",
        evidence="The permuter moves register allocation and never touches "
                 "types or control flow. unlockRelocatableHeapBlock sat at "
                 "99.167% purely because the model declared an 18-byte struct "
                 "against a real 20-byte one; 300s of permuting moved it "
                 "nowhere. Confirmed by the repo's own skill docs.",
        tested_on="unlockRelocatableHeapBlock, 300s",
        date="2026-08-27",
        keywords=["permuter", "permute", "register allocation", "near-miss"],
    ),
    Hypothesis(
        id="array-stride-decoding",
        claim="Struct size can be read arithmetically out of the target's "
              "index arithmetic and handed to the model as fact.",
        source="diagnosis of unlockRelocatableHeapBlock",
        status="CONFIRMED",
        evidence="`sll 2; addu; sll 2` is unambiguously x20. That function went "
                 "from stuck-at-99.167% (permuter could not help) to EXACT on "
                 "the first draw once the stride was stated.",
        tested_on="unlockRelocatableHeapBlock, lockRelocatableHeapBlock",
        date="2026-08-27",
        keywords=["stride", "struct size", "array", "index", "sll"],
    ),
    Hypothesis(
        id="narrow-param-homing",
        claim="A dead `sw $aN, K($sp)` plus `andi 0xffff` at entry proves "
              "parameter N is 16-bit.",
        source="DECOMPILATION_LEARNINGS.md, parameter homing section",
        status="CONFIRMED",
        evidence="setRaceCameraMode(u16,u16) shows the pattern twice; the s32 "
                 "control getRaceItemEffectType shows neither instruction. "
                 "Detector verified on both, plus a deliberate conservative "
                 "miss on spawnPatrolCourseObject.",
        tested_on="3 functions, positive and negative controls",
        date="2026-08-27",
        keywords=["parameter", "narrow", "homing", "andi", "u16", "s16"],
    ),
    Hypothesis(
        id="blank-line-reflow",
        claim="Statement line placement or blank lines change codegen.",
        source="general decomp folklore",
        status="REFUTED",
        evidence="The reference repo ran the control: this project builds "
                 "without -g, so inserting blank lines anywhere yields a "
                 "byte-identical object. Guidance suggesting otherwise assumes "
                 "a debug-bearing build.",
        tested_on="reference repo's own control experiment",
        date="2026-08-27",
        keywords=["blank line", "line number", "reflow", "formatting"],
    ),
    Hypothesis(
        id="ground-truth-in-prompt",
        claim="Feeding the workspace's generated ctx.c gives useful type context.",
        source="naive setup",
        status="REFUTED",
        evidence="ctx.c contains the preprocessed translation unit INCLUDING "
                 "the target function's own body -- line 2326 was literally the "
                 "answer to getRaceItemEffectType. Using it fabricates a 100% "
                 "and invalidates every downstream number. Harmless upstream "
                 "where the function is genuinely undone; fatal for evaluation.",
        tested_on="inspection of the generated ctx.c",
        date="2026-08-27",
        keywords=["ctx", "context", "contamination", "ground truth"],
    ),
    Hypothesis(
        id="byte-cast-shift-pair",
        claim="`sll 24` followed by `srl 24` is a u8 cast; with `sra 24` it is "
              "an s8 cast -- so the variable should be declared 8-bit.",
        source="OoT -O2 IDO 5.3 guide",
        status="UNTESTED",
        evidence="",
        keywords=["byte", "u8", "s8", "cast", "sll 24", "shift"],
    ),
    Hypothesis(
        id="void-param-frame-size",
        claim="`void f(void)` uses 4 more bytes of stack than `void f()`.",
        source="OoT -O2 IDO 5.3 guide",
        status="UNTESTED",
        evidence="",
        keywords=["frame", "stack", "void", "prototype", "frame size"],
    ),
    Hypothesis(
        id="comparison-normalization",
        claim="`x > y` is emitted as `x >= y + 1` when y is a constant.",
        source="OoT -O2 IDO 5.3 guide",
        status="UNTESTED",
        evidence="",
        keywords=["comparison", "slti", "greater", "constant"],
    ),
    Hypothesis(
        id="array-index-loop-form",
        claim="`&a[i]` inside a loop makes IDO keep an extra loop counter; "
              "`a + i` uses multiplication instead.",
        source="OoT -O2 IDO 5.3 guide",
        status="UNTESTED",
        evidence="",
        keywords=["loop", "array", "index", "pointer", "counter"],
    ),
    Hypothesis(
        id="loop-unrolling",
        claim="IDO unrolls small loops by 2 or 4; `continue;` or `i++; i--;` "
              "suppresses it.",
        source="OoT -O2 IDO 5.3 guide",
        status="UNTESTED",
        evidence="",
        keywords=["unroll", "loop", "continue"],
    ),
]


def load() -> list[Hypothesis]:
    if not BANK.exists():
        save(SEED)
        return list(SEED)
    return [Hypothesis(**h) for h in json.loads(BANK.read_text())]


def save(items: list[Hypothesis]) -> None:
    BANK.write_text(json.dumps([asdict(h) for h in items], indent=2))


def check(query: str) -> list[Hypothesis]:
    """Anything already tested that resembles this idea."""
    q = query.lower()
    hits = []
    for h in load():
        hay = " ".join([h.id, h.claim, " ".join(h.keywords)]).lower()
        if any(tok in hay for tok in q.split()):
            hits.append(h)
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", help="search before implementing an idea")
    ap.add_argument("--status", help="list by status")
    ap.add_argument("--add", nargs=4, metavar=("ID", "CLAIM", "SOURCE", "STATUS"))
    ap.add_argument("--evidence", default="")
    args = ap.parse_args()

    if args.add:
        items = load()
        items.append(Hypothesis(id=args.add[0], claim=args.add[1],
                                source=args.add[2], status=args.add[3],
                                evidence=args.evidence))
        save(items)
        print(f"recorded {args.add[0]} as {args.add[3]}")
        return

    items = check(args.check) if args.check else load()
    if args.status:
        items = [h for h in items if h.status == args.status.upper()]

    if not items:
        print("no matching hypotheses -- this idea appears untested")
        return

    for h in items:
        print(f"\n[{h.status}] {h.id}")
        print(f"  claim  : {h.claim}")
        print(f"  source : {h.source}")
        if h.evidence:
            print(f"  result : {h.evidence}")
        if h.tested_on:
            print(f"  tested : {h.tested_on} ({h.date})")

    counts: dict[str, int] = {}
    for h in load():
        counts[h.status] = counts.get(h.status, 0) + 1
    print("\n" + "  ".join(f"{k}:{v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
