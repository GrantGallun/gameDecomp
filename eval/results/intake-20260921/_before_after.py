"""Before/after on ONE frame: the intake sequence, the module fix, and the derived-widths arm.

The claim to defend is that the code changed and the sample did not. So this prints, per arm, the frame's
identity (function set + draft hashes) before it prints any rate. Any arm whose frame identity differs
from the others is a different measurement and is reported as such.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")
ARMS = [("class-replay.json", "before: intake order, placeholder step missing"),
        ("class-replay-fixed.json", "after: campaign order, module fixed"),
        ("class-replay-widths.json", "after + target-derived widths")]


def load(name: str):
    path = BASE / name
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def identity(payload) -> dict:
    return {r["function"]: (r.get("draft_sha256") or "")[:16] for r in payload["rows"]}


print(f"{'arm':44} {'n':>3} {'conv':>5} {'exact':>6} {'rate':>7} {'sec':>6}")
frames = {}
for name, label in ARMS:
    payload = load(name)
    if payload is None:
        print(f"{label:44} MISSING ({name})")
        continue
    frames[label] = identity(payload)
    print(f"{label:44} {payload['front_door_failures']:3} "
          f"{payload['sequence_converted']:5} {payload['sequence_exact']:6} "
          f"{payload['conversion_rate']:7.3f} {payload['seconds']:6.1f}")

labels = list(frames)
if len(labels) > 1:
    base = labels[0]
    print(f"\nframe identity (function -> draft hash) against '{base}':")
    for label in labels[1:]:
        same_set = set(frames[label]) == set(frames[base])
        differing = [f for f in frames[base]
                     if f in frames[label] and frames[base][f] != frames[label][f]]
        print(f"   {label:44} same set={same_set} differing drafts={len(differing)}")
        for function in differing[:6]:
            print(f"      {function}: {frames[base][function]} -> {frames[label][function]}")

print("\nper-action, per arm (fired / compiled / exact / declined / gated / crashed):")
for name, label in ARMS:
    payload = load(name)
    if payload is None:
        continue
    print(f"   {label}")
    for action, stat in sorted(payload["per_action"].items()):
        short = action.split(".")[-1]
        print(f"      {short:22} fired={stat.get('fired', 0):3} compiled={stat.get('compiled', 0):3} "
              f"exact={stat.get('exact', 0):3} declined={stat.get('declined', 0):3} "
              f"gated={stat.get('gated', 0):3} crashed={stat.get('crashed', 0):3}")

print("\nderived widths: names the target could type, and what happened where it could not")
widths = load("class-replay-widths.json")
fixed = load("class-replay-fixed.json")
if widths and fixed:
    for row_w, row_f in zip(widths["rows"], fixed["rows"]):
        names = row_w.get("placeholder_widths") or []
        receipt = row_w.get("placeholder_width_receipt") or []
        if not names and not receipt:
            continue
        derived = [r.get("name") for r in receipt if r.get("basis") == "derived"]
        print(f"   {row_w['function']:40} derived={len(derived):2} {derived[:6]}"
              f"{'  ...' if len(derived) > 6 else ''}")
        if row_w["sequence"]["compiled"] != row_f["sequence"]["compiled"]:
            print(f"      VERDICT DIFFERS from the default arm: "
                  f"{row_f['sequence']} -> {row_w['sequence']}")
