"""Test register-allocator rules against IDO 5.3's own uopt trace (protocol: eval/results/allocator-rules-20260923).

Zero-compile hypotheses H1-H4 run over stored level-5/6 trace pairs. Each returns counts and counterexamples;
verdicts apply the thresholds fixed in PROTOCOL.md before the run.

    python -m eval.allocator_rules TRACE_DIR --out DIR
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import random

from solver import uopt_trace


def units(raw: int) -> int:
    return raw if raw < 3 else ((raw - 2) >> 2) + 2


def span(record) -> int:
    return len({row[0] for row in record.blocks} | set(record.default_blocks))


def fits_h1(adjsave: float, nblocks: int, max_refs: int = 400) -> int | None:
    """The smallest reference count that reproduces adjsave under the 7.1 formula, or None."""
    for r in range(1, max_refs + 1):
        value = 10 * r / units(r + nblocks)
        if abs(value - adjsave) <= 1e-4 * max(1.0, abs(adjsave)):
            return r
    return None


def procedures(trace_dir: Path):
    for l5 in sorted(trace_dir.glob("*.l5")):
        l6 = l5.with_suffix(".l6")
        if l6.exists():
            yield from uopt_trace.join(l5.read_text(errors="replace"), l6.read_text(errors="replace")).values()


def h1(procs) -> dict:
    points = [(r.adjsave, span(r)) for p in procs for r in p.ranges.values()
              if r.adjsave is not None and r.adjsave == r.adjsave and r.adjsave > 0 and span(r) > 0]
    hits = [fits_h1(a, n) for a, n in points]
    rng = random.Random(20260923)
    shuffled_spans = [n for _a, n in points]
    rng.shuffle(shuffled_spans)
    shuffled = sum(fits_h1(a, n) is not None for (a, _n), n in zip(points, shuffled_spans))
    misses = [(a, n) for (a, n), h in zip(points, hits) if h is None]
    refs = [h for h in hits if h is not None]
    return {"ranges": len(points), "fit": len(refs), "fit_rate": round(len(refs) / max(1, len(points)), 4),
            "shuffled_rate": round(shuffled / max(1, len(points)), 4),
            "reference_count_quartiles": sorted(refs)[len(refs) // 4:: max(1, len(refs) // 4)][:4] if refs else [],
            "misses_sample": misses[:15]}


def h1_prime(procs) -> dict:
    """POST-HOC (read off H1's first 15 misses): adjsave = integer save / units(block span alone)."""
    points = [(r.adjsave, span(r)) for p in procs for r in p.ranges.values()
              if r.adjsave is not None and r.adjsave == r.adjsave and r.adjsave > 0 and span(r) > 0]

    def integral(a, n):
        s = a * units(n)
        return abs(s - round(s)) <= 1e-3 * max(1.0, s)
    rng = random.Random(20260923)
    spans = [n for _a, n in points]
    rng.shuffle(spans)
    saves = [round(a * units(n)) for a, n in points if integral(a, n)]
    residue = {k: sum(s % 10 == k for s in saves) for k in range(10)}
    return {"ranges": len(points), "fit_rate": round(sum(integral(a, n) for a, n in points) / max(1, len(points)), 4),
            "shuffled_rate": round(sum(integral(a, n) for (a, _n), n in zip(points, spans)) / max(1, len(points)), 4),
            "save_mod_10": residue,
            "misses_sample": [(a, n) for a, n in points if not integral(a, n)][:12]}


def h2(procs) -> dict:
    stats = {"constrained": [0, 0], "unconstrained": [0, 0], "unconstrained_lr_order": [0, 0], "ties": [0, 0]}
    for p in procs:
        seq = [(d, p.ranges.get(d.piece)) for d in p.decisions if d.outcome != "not_colored"]
        for (a, ra), (b, rb) in zip(seq, seq[1:]):
            if not ra or not rb or ra.adjsave is None or rb.adjsave is None:
                continue
            if a.outcome == b.outcome:
                cell = stats[a.outcome]
                cell[0] += 1
                cell[1] += rb.adjsave <= ra.adjsave
                if a.outcome == "unconstrained":
                    stats["unconstrained_lr_order"][0] += 1
                    stats["unconstrained_lr_order"][1] += b.piece > a.piece
                elif rb.adjsave == ra.adjsave:
                    stats["ties"][0] += 1
                    stats["ties"][1] += b.piece > a.piece
    return {k: {"pairs": n, "consistent": c, "rate": round(c / n, 4) if n else None} for k, (n, c) in stats.items()}


def h3(procs) -> dict:
    n = lowest = model = 0
    disagreements = []
    for p in procs:
        for d in p.decisions:
            record = p.ranges.get(d.piece)
            band = uopt_trace.band_of(d.color) if d.outcome != "not_colored" else None
            if not record or not band:
                continue
            first, end, _arg = uopt_trace.BANDS[band]
            n += 1
            simple = uopt_trace._scan(first, end, record.forbidden or frozenset())
            lowest += simple == d.color
            model += uopt_trace.select_colour(record, band) == d.color
            if simple != d.color and len(disagreements) < 10:
                disagreements.append({"function": p.name, "lr": d.piece, "chosen": d.color, "lowest_free": simple,
                                      "preferences": record.preferences(), "kind": record.kind})
    return {"decisions": n, "lowest_free_rate": round(lowest / max(1, n), 4),
            "model_5_3_rate": round(model / max(1, n), 4), "lowest_free_misses_sample": disagreements}


def h4(procs) -> dict:
    cm = {"share_interfere": 0, "share_no": 0, "disjoint_interfere": 0, "disjoint_no": 0}
    examples = {"share_no": [], "disjoint_interfere": []}
    for p in procs:
        ranges = {k: r for k, r in p.ranges.items() if r.blocks or r.default_blocks}
        edges = {frozenset((k, j)) for k, r in ranges.items() for j in r.interferes}
        blocks = {k: {row[0] for row in r.blocks} | set(r.default_blocks) for k, r in ranges.items()}
        for a, b in itertools.combinations(sorted(ranges), 2):
            share = bool(blocks[a] & blocks[b])
            listed = frozenset((a, b)) in edges
            key = f"{'share' if share else 'disjoint'}_{'interfere' if listed else 'no'}"
            cm[key] += 1
            if key in examples and len(examples[key]) < 8:
                examples[key].append({"function": p.name, "ranges": [a, b], "kinds": [ranges[a].kind, ranges[b].kind]})
    share = cm["share_interfere"] + cm["share_no"]
    disjoint = cm["disjoint_interfere"] + cm["disjoint_no"]
    return {**cm, "p_interfere_given_share": round(cm["share_interfere"] / max(1, share), 4),
            "p_no_given_disjoint": round(cm["disjoint_no"] / max(1, disjoint), 4), "examples": examples}


def verdicts(r: dict) -> dict:
    return {
        "H1": "confirmed" if r["H1"]["fit_rate"] >= 0.95 and r["H1"]["fit_rate"] - r["H1"]["shuffled_rate"] >= 0.30
              else "refuted",
        "H2_constrained": "confirmed" if (r["H2"]["constrained"]["rate"] or 0) >= 0.95 else "refuted",
        "H2_unconstrained_by_priority": "confirmed" if (r["H2"]["unconstrained"]["rate"] or 0) >= 0.95 else "refuted",
        "H2_ties_by_order": "confirmed" if (r["H2"]["ties"]["rate"] or 0) >= 0.95 else "refuted",
        "5.3_unconstrained_by_live_range": "confirmed" if (r["H2"]["unconstrained_lr_order"]["rate"] or 0) >= 0.95 else "refuted",
        "H3_lowest_free": "confirmed" if r["H3"]["lowest_free_rate"] >= 0.95 else "refuted",
        "H4": "confirmed" if min(r["H4"]["p_interfere_given_share"], r["H4"]["p_no_given_disjoint"]) >= 0.95 else "refuted",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("traces", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    procs = list(procedures(args.traces))
    result = {"procedures": len(procs), "H1": h1(procs), "H1_prime_posthoc": h1_prime(procs),
              "H2": h2(procs), "H3": h3(procs), "H4": h4(procs)}
    result["verdicts"] = verdicts(result)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "results.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({k: v for k, v in result.items() if k != "H4"} | {"H4": {k: v for k, v in result["H4"].items()
                                                                              if k != "examples"}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
