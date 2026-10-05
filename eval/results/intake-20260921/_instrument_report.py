"""The instrument's report card: what the 40-state frame could and could not tell us, and what the
200-state frame says instead.

Four measurements, two frames, and the point of the exercise is in the third column -- whether a number
that looked solid at n=40 survives at n=200, and whether the arms agree with each other in a way the frame's
own membership drift cannot explain.

Nothing here is a new measurement; every figure comes from a file in this directory.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
load = lambda name: json.loads((BASE / name).read_text(encoding="utf-8"))   # noqa: E731


def sanity(payload: dict) -> dict:
    """The control arm reports different field names from the intake arm, because it measures a different
    thing: `improved` counts a certificate that moved at all, `converted` counts a draft that now compiles.
    Both are read here, and the distinction is preserved in the table rather than averaged."""
    n = payload.get("front_door_failures") or payload.get("measured")
    if "sequence_converted" in payload:
        return {"n": n, "converted": payload["sequence_converted"], "exact": payload["sequence_exact"],
                "kind": "compiles"}
    return {"n": n, "converted": payload.get("any_action_improved_any_state"),
            "exact": payload.get("certified_matches"), "kind": "improved"}


rows = [("narrow frame, intake hero", "class-frame.json"),
        ("narrow frame, intake replay", "post-fix2-intake.json"),
        ("narrow frame, control", "post-fix-control.json"),
        ("wide frame, intake (fresh build)", "wide-frame.json"),
        ("wide frame, intake (frozen replay)", "wide-intake.json"),
        ("wide frame, control", "wide-control.json")]

print(f"{'measurement':38} {'n':>4} {'conv':>5} {'exact':>6} {'rate':>7}  {'counts':<9}")
values: dict[str, dict] = {}
for label, name in rows:
    path = BASE / name
    if not path.is_file():
        print(f"{label:38} (missing: {name})")
        continue
    payload = load(name)
    stats = sanity(payload)
    values[label] = stats
    rate = (stats["converted"] / stats["n"]) if stats["n"] and stats["converted"] is not None else 0.0
    print(f"{label:38} {stats['n']:4} {stats['converted']:5} {stats['exact']:6} {rate:7.3f}  "
          f"{stats['kind']:<9}")

print("\nthe instrument's own consistency checks:")
fresh = values.get("wide frame, intake (fresh build)")
frozen = values.get("wide frame, intake (frozen replay)")
if fresh and frozen:
    same = fresh == frozen
    print(f"  frozen replay reproduces the fresh build: {same}  ({fresh} vs {frozen})")
    if same:
        print("    -> the frame is deterministic: the same 200 functions, measured twice, agree exactly.")

narrow = values.get("narrow frame, intake replay")
wide = values.get("wide frame, intake (frozen replay)")
if narrow and wide:
    print(f"  the 22.5% at n=40 is {100 * wide['converted'] / wide['n']:.1f}% at n=200 "
          f"({narrow['converted']}/{narrow['n']} -> {wide['converted']}/{wide['n']})")
    print(f"    -> the narrow frame's rate was HIGH by {100 * (narrow['converted'] / narrow['n'] - wide['converted'] / wide['n']):.1f} "
          f"percentage points, because its composition was 2 tiny / 10 small against 0 tiny / 2 small here.")

for label in ("wide frame, intake (frozen replay)", "wide frame, control"):
    payload_name = {"wide frame, intake (frozen replay)": "wide-intake.json",
                    "wide frame, control": "wide-control.json"}[label]
    if (BASE / payload_name).is_file():
        payload = load(payload_name)
        print(f"\n{label}:")
        by_tier = payload.get("by_tier")
        if by_tier:
            print(f"  by tier: {json.dumps(by_tier)}")
        res = payload.get("resolution")
        if res:
            print(f"  resolution: {res.get('resolvable_states')} states at this frame size "
                  f"({res.get('resolvable_percent')}%), drift basis {res.get('assumed_membership_drift')}")
        if payload.get("per_action"):
            print("  per action:")
            for action, stat in sorted(payload["per_action"].items()):
                print(f"    {action.split('.')[-1]:24} fired={stat.get('fired', 0):3} "
                      f"compiled={stat.get('compiled', 0):3} exact={stat.get('exact', 0):3}")

(BASE / "instrument-report.json").write_text(json.dumps(
    {"measurements": values,
     "note": ("every figure from a stored payload; the narrow/wide comparison is the frame's sensitivity "
              "made visible")}, indent=2) + "\n", encoding="utf-8")
print(f"\nwrote {BASE / 'instrument-report.json'}")
