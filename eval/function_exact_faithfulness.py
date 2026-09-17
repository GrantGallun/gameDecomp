"""Is `function_exact` faithful, or only exact-looking? Answer from the certificates, not from intent.

Operator: "it has to be exact -- I don't want unwanted behavior, because then it's not a faithful
recreation." So the tier has to justify byte identity, not resemble it. The certificate records what
it compared; this reads it back for each of the 11 and prints the three things that decide faithfulness:

  schema + relocation_policy   what the comparison actually covers
  function_bytes               how much of the ROM was compared against the candidate
  target/candidate trailing    the bytes BEYOND the function in each section -- if the section
                               difference is entirely trailing material the candidate never emits,
                               the difference cannot be behaviour, because the function's own words
                               and its resolved references are the ROM's own

    python3 eval/function_exact_faithfulness.py
"""
from __future__ import annotations

import json
import pathlib

REPO = pathlib.Path.home() / "decomp" / "sbk1"

NAMES = [
    "calculateFixedAngleBetweenXZPoints", "drawMainMenuModeSelectMenuOptions",
    "fadeInRaceGameplayViewports", "func_8005905C", "initControllerPakFileDeleteFlow",
    "initMainMenu", "initRaceTypeSelectMenu", "osSpTaskStartGo", "rmonPrintf",
    "updateRaceCameraMenuPreview", "updateRacePlayerPostUpdateAttack",
]

FIELDS = ("schema_version", "scope", "relocation_policy", "function_bytes",
          "target_trailing_bytes", "candidate_trailing_bytes", "annotated_trailing_bytes",
          "assembler_alignment_bytes", "rom_offset")

for name in NAMES:
    ws = REPO / "nonmatchings" / name
    certs = [p for p in ws.glob("*.verification.json")]
    best = None
    for path in certs:
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        b = d.get("function_boundary") or {}
        if b.get("function_exact"):
            best = (path.name, d, b)
    print(f"=== {name} ===")
    if not best:
        print("   NO function_exact certificate on disk")
        continue
    fname, d, b = best
    print(f"   {fname}")
    for key in FIELDS:
        if key in b:
            print(f"   boundary.{key:<26} {b[key]}")
    print(f"   object status{'':<20} {d.get('status')}")
