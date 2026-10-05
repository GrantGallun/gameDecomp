"""Was the narrow frame's rate flattered by its composition? Standardise the wide frame onto it.

Both frames measure the same code on the same failure class, so the difference between 22.5% (n=40) and
10.0% (n=200) has two candidate explanations and they have different consequences:

  composition   the narrow frame was 2 tiny / 10 small / 10 medium / 9 large / 9 huge; the wide frame is
                0 / 2 / 34 / 57 / 107. If the per-tier rates are similar, the headline difference is the
                frame's size mix and nothing about the lever changed.
  population    if the per-tier rates ALSO fall, then the wide frame is drawing harder states inside each
                tier -- the frame is a different population, not a differently-shaped sample of one.

Direct standardisation answers it: apply the wide frame's per-tier rates to the narrow frame's tier counts.
That is the rate the narrow frame would have shown had it been drawn like the wide one.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
load = lambda name: json.loads((BASE / name).read_text(encoding="utf-8"))   # noqa: E731

narrow = load("post-fix2-intake.json")["by_tier"]
wide = load("wide-intake.json")["by_tier"]
TIERS = ("tiny", "small", "medium", "large", "huge")

print(f"{'tier':8} {'narrow n':>9} {'narrow conv':>12} {'narrow rate':>12} "
      f"{'wide n':>7} {'wide conv':>10} {'wide rate':>10}")
for tier in TIERS:
    a, b = narrow[tier], wide[tier]
    ra = f"{a['converted'] / a['n']:.3f}" if a["n"] else "  --"
    rb = f"{b['converted'] / b['n']:.3f}" if b["n"] else "  --"
    print(f"{tier:8} {a['n']:9} {a['converted']:12} {ra:>12} {b['n']:7} {b['converted']:10} {rb:>10}")

narrow_n = sum(narrow[t]["n"] for t in TIERS)
narrow_conv = sum(narrow[t]["converted"] for t in TIERS)
wide_n = sum(wide[t]["n"] for t in TIERS)
wide_conv = sum(wide[t]["converted"] for t in TIERS)
print(f"\nobserved: narrow {narrow_conv}/{narrow_n} = {narrow_conv / narrow_n:.4f}   "
      f"wide {wide_conv}/{wide_n} = {wide_conv / wide_n:.4f}")

# Direct standardisation: the wide frame's per-tier rates, weighted by the NARROW frame's tier counts.
standardised_num = 0.0
for tier in TIERS:
    b = wide[tier]
    if b["n"]:
        standardised_num += (b["converted"] / b["n"]) * narrow[tier]["n"]
standardised = standardised_num / narrow_n
print(f"\nwide per-tier rates on the narrow composition: {standardised_num:.2f}/{narrow_n} "
      f"= {standardised:.4f}")
print(f"  narrow observed  : {narrow_conv / narrow_n:.4f}")
print(f"  residual         : {(narrow_conv / narrow_n) - standardised:+.4f} "
      f"({(narrow_conv / narrow_n) - standardised:+.3f} in rate, "
      f"{(narrow_conv / narrow_n - standardised) * narrow_n:+.1f} states)")

verdict = ("COMPOSITION explains it: standardising the wide frame onto the narrow one reproduces its rate "
           f"to {abs((narrow_conv / narrow_n) - standardised) * 100:.1f} percentage points."
           if abs((narrow_conv / narrow_n) - standardised) <= 0.05 else
           "COMPOSITION does not fully explain it: a residual remains after standardisation, so the wide "
           "frame is drawing harder states inside the tiers.")
print(f"\n{verdict}")

(BASE / "instrument-standardisation.json").write_text(json.dumps(
    {"narrow": narrow, "wide": wide,
     "observed": {"narrow": narrow_conv / narrow_n, "wide": wide_conv / wide_n},
     "standardised_wide_on_narrow": standardised,
     "residual": (narrow_conv / narrow_n) - standardised,
     "verdict": verdict,
     "note": "direct standardisation of the wide frame's per-tier rates onto the narrow frame's tier counts"},
    indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'instrument-standardisation.json'}")
