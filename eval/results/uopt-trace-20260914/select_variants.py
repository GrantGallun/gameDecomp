"""Score selection-rule variants over census traces (reproduces the README variant table).

    python3 eval/results/uopt-trace-20260914/select_variants.py [CENSUS_DIR]
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import uopt_trace as U

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "decomp/tools-src/uopt-trace-census"
LIVE = re.compile(r"^- live bb -\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)")
DEFAULT = re.compile(r"^- live bb \(default\) \(\s*\d+\)\s*(\[.*\])")


def blocks(text):
    """(procedure, lr) -> (rows [(bb, x, y, pref)], default-block set)."""
    out = defaultdict(lambda: ([], set()))
    proc = lr = None
    for line in text.splitlines():
        t = U._TIMING.search(line)
        if t:
            proc, lr = t.group(1), None
        elif (a := U._ACTIVE.search(line)):
            lr = int(a.group(3))
        elif lr is not None and (m := LIVE.match(line)):
            out[(proc, lr)][0].append(tuple(int(x) for x in m.groups()))
        elif lr is not None and (m := DEFAULT.match(line)):
            out[(proc, lr)][1].update(U.parse_set(m.group(1)))
        elif line.startswith("% % % node"):
            lr = None
    return out


def scan(start, end, forb):
    return next((c for c in range(start, end) if c not in forb), None)


def predict(g, rows, default, color, variant):
    forb = g.forbidden or frozenset()
    if color >= 14:
        return scan(14, 23, forb)
    prefs = []
    for bb, _x, _y, pref in rows:
        if pref > 0 and pref not in prefs and (variant["band_prefs"] is False or pref < 14):
            prefs.append(pref)
    if not variant.get("use_prefs", True):
        prefs = []
    if prefs:
        free = [p for p in prefs if p not in forb]
        if variant["try_later_prefs"] and free:
            return free[0]
        if not variant["try_later_prefs"] and prefs[0] not in forb:
            return prefs[0]
        return scan(prefs[0], 14, forb)
    at_entry = any(bb == 0 for bb, *_ in rows) or 0 in default
    if variant.get("param_rule", True) and g.kind == "P" and (at_entry or not variant["param_needs_entry"]):
        return scan(3, 14, forb)
    return scan(1, 14, forb)


VARIANTS = {
    "lowest free only": dict(try_later_prefs=True, param_needs_entry=True, band_prefs=True, use_prefs=False,
                             param_rule=False),
    "prefs, no parameter rule": dict(try_later_prefs=True, param_needs_entry=True, band_prefs=True, param_rule=False),
    "prefs(later ok) + P@entry scan a0 + band prefs": dict(try_later_prefs=True, param_needs_entry=True, band_prefs=True),
    "prefs(first only) + P@entry + band prefs": dict(try_later_prefs=False, param_needs_entry=True, band_prefs=True),
    "prefs(later ok) + any P + band prefs": dict(try_later_prefs=True, param_needs_entry=False, band_prefs=True),
    "prefs(later ok) + P@entry, all prefs": dict(try_later_prefs=True, param_needs_entry=True, band_prefs=False),
}

if __name__ == "__main__":
    score, total, misses = Counter(), Counter(), defaultdict(list)
    for l5 in sorted((OUT / "traces").glob("*.l5")):
        text = l5.read_text(errors="replace")
        rowmap = blocks(text)
        for name, proc in U.join(text, l5.with_suffix(".l6").read_text(errors="replace")).items():
            for d in proc.decisions:
                if d.outcome == "not_colored" or not (1 <= d.color < 23):
                    continue
                g = proc.ranges.get(d.piece)
                if g is None:
                    continue
                rows, default = rowmap.get((name, d.piece), ([], set()))
                band = "callee" if d.color >= 14 else "caller"
                total[band] += 1
                for label, variant in VARIANTS.items():
                    p = predict(g, rows, default, d.color, variant)
                    score[(label, band)] += p == d.color
                    if p != d.color and band == "caller":
                        misses[label].append((l5.name.split("__")[-1], name, d.piece, d.color, p, sorted(g.forbidden or []),
                                              g.kind, g.offset, rows[:4], sorted(default)[:6]))
    for label in VARIANTS:
        print(f"{label:48} caller {score[(label, 'caller')]}/{total['caller']}  callee {score[(label, 'callee')]}/{total['callee']}")
    best = next(iter(VARIANTS))
    for m in misses[best]:
        print("MISS", m)
