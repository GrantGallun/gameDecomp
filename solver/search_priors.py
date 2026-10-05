"""Learned edit-family priorities for site_edits.search: which families improved residuals like this one.

The search's proposal order is hand-set (ORDER, the shape lane, round-robin). This replaces it, opt-in, with a table
fitted from recorded search events: for each residual FEATURE (a rule principles.residual_rules diagnoses, and the
earliest fault class residual_classes.counts still finds), how often each edit FAMILY improved the parent's gradient
per compile. `order` is a stable reorder of one parent's proposals by the best smoothed rate among the parent's
features; nothing is removed unless `drop=True`, and even then only a family with `min_tries` recorded attempts
and zero improvements under EVERY feature the residual shows.

Provenance rule: a table is fitted on a training pool only, never on the cases it is scored on
(eval/results/edit-capability-20261002/trails.py fits on multi_train and refuses dev/held-out). Its receipt records
the event count and the files it came from. Reordering cannot make a candidate that was never proposed; it changes
what a fixed budget reaches.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

PREFIXED = ("shape", "pool")      # families whose second segment names a distinct generator


def family(kind_or_label: str) -> str:
    """`shape:empty_arm` / `pool:<kind>` keep two segments; every other family (incl. `mined:<rule>`) keeps one.
    Accepts either an Edit.kind or the `kind:label` string search() passes to score()."""
    parts = kind_or_label.split(":")
    if parts[0] in PREFIXED and len(parts) > 1:
        return f"{parts[0]}:{parts[1]}"
    return parts[0]


def features(diff: str) -> list[str]:
    from solver import principles, residual_classes
    out = [f"rule:{rule}" for rule, _reason in principles.residual_rules(diff or "")]
    counts = residual_classes.counts(diff or "")
    first = next((c for c in residual_classes.CLASSES if counts.get(c)), None)
    out.append(f"class:{first or 'none'}")
    return out


def fit(events, *, sources=()) -> dict:
    """events: dicts with `features` (list), `family`, `improved` (bool)."""
    by_feature: dict[str, dict[str, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    overall: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    n = 0
    for e in events:
        n += 1
        for f in e["features"]:
            by_feature[f][e["family"]][0] += bool(e["improved"])
            by_feature[f][e["family"]][1] += 1
        overall[e["family"]][0] += bool(e["improved"])
        overall[e["family"]][1] += 1
    return {"version": 1, "prior": [1.0, 4.0], "events": n, "sources": list(sources),
            "by_feature": {f: dict(v) for f, v in by_feature.items()}, "overall": dict(overall)}


def rate(table: dict, feats, fam: str) -> float:
    a, b = table["prior"]
    rates = [(s[0] + a) / (s[1] + a + b) for f in feats if (s := table["by_feature"].get(f, {}).get(fam))]
    if rates:
        return max(rates)
    s = table["overall"].get(fam, [0, 0])
    return (s[0] + a) / (s[1] + a + b)


def dead(table: dict, feats, fam: str, min_tries: int) -> bool:
    stats = [table["by_feature"].get(f, {}).get(fam) for f in feats]
    return bool(feats) and all(s and s[1] >= min_tries and s[0] == 0 for s in stats)


def orderer(table: dict, *, drop: bool = False, min_tries: int = 8):
    """A `site_edits.search(reorder=...)` callable: (parent diff, proposals) -> proposals."""
    def order(diff, edits):
        feats = features(diff)
        keep = [e for e in edits if not (drop and dead(table, feats, family(e.kind), min_tries))]
        ranked = sorted(enumerate(keep), key=lambda ie: (-rate(table, feats, family(ie[1].kind)), ie[0]))
        return [e for _i, e in ranked]
    return order


def save(table: dict, path) -> None:
    Path(path).write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
