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




def load() -> list[Hypothesis]:
    """The bank as committed. There is deliberately no in-code fallback.

    There used to be a SEED list here that `load()` wrote out whenever the JSON
    was missing, and it drifted: `array-stride-decoding` and
    `narrow-param-homing` had been downgraded to UNTESTED in the bank while the
    seed still called them CONFIRMED, and `byte-cast-shift-pair` disagreed the
    other way. A rebuild would have resurrected two retired conclusions and
    handed them back the right to steer the solver -- the precise failure this
    module exists to prevent, inside the module itself.

    `patterns/hypotheses.json` is tracked in git, so restoring it is a
    checkout, not a regeneration.
    """
    if not BANK.exists():
        raise FileNotFoundError(
            f"{BANK} is missing. It is tracked in git -- restore it with "
            "`git checkout -- patterns/hypotheses.json`. Regenerating it "
            "would silently discard every result recorded in it.")
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
