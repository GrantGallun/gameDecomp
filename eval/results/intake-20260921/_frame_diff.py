"""Why did the frame build convert 2 and the frozen replay of the same frame convert 0?

A frozen replay that disagrees with the build it froze is either a real nondeterminism in the intake
mechanisms or a defect in the replay path. Both are findings; neither may be averaged away. This prints
the per-function difference and the draft hashes so the two arms can be shown to have started from the
same bytes.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path("/mnt/c/Code/gameDecomp/eval/results/intake-20260921")


def load(name: str) -> dict:
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def sig(payload: dict) -> dict:
    return {r["function"]: {"compiled": r["sequence"]["compiled"],
                            "exact": r["sequence"]["exact"],
                            "score": r["sequence"]["score"],
                            "draft": (r.get("draft_sha256") or "")[:12],
                            "fired": tuple(sorted(k.split(".")[-1] for k, v in r["actions"].items()
                                                  if v.get("changed")))}
            for r in payload["rows"]}


build, replay = load("class-frame.json"), load("class-replay.json")
a, b = sig(build), sig(replay)
print(f"build rows={len(a)} converted={build['sequence_converted']} exact={build['sequence_exact']}")
print(f"replay rows={len(b)} converted={replay['sequence_converted']} exact={replay['sequence_exact']}")
print(f"same function set: {set(a) == set(b)}")

differing = [name for name in a if name in b and a[name] != b[name]]
print(f"\nfunctions whose outcome differs: {len(differing)}")
for name in differing:
    print(f"  {name}")
    print(f"     build : {a[name]}")
    print(f"     replay: {b[name]}")

drift = [name for name in a if name in b and a[name]["draft"] != b[name]["draft"]]
print(f"\ndraft hash differs: {len(drift)} {drift}")

converted = [name for name, v in a.items() if v["compiled"]]
print(f"\nconverted in the build: {converted}")
for name in converted:
    print(f"  {name}: build fired={a[name]['fired']} replay fired={b.get(name, {}).get('fired')}")
    for row in build["rows"]:
        if row["function"] == name:
            for label, entry in row["actions"].items():
                if entry.get("changed"):
                    print(f"     build {label.split('.')[-1]:16} compiled={entry.get('compiled')} "
                          f"exact={entry.get('exact')} score={entry.get('score')} "
                          f"stderr={(entry.get('stderr') or '')[:80]}")
    for row in replay["rows"]:
        if row["function"] == name:
            for label, entry in row["actions"].items():
                if entry.get("changed"):
                    print(f"     replay {label.split('.')[-1]:15} compiled={entry.get('compiled')} "
                          f"exact={entry.get('exact')} score={entry.get('score')} "
                          f"stderr={(entry.get('stderr') or '')[:80]}")
