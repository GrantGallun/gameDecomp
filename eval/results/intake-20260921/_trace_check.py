"""Check the probe's new trace surface imports and classifies correctly.

Reads the classifier and the trace helper directly, so a syntax or import error in the probe is caught
before a 200-state run is started.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

import eval.intake_probe as probe                                         # noqa: E402

print("import ok; sequence is:")
for step in probe.SEQUENCE:
    print(f"   {step.split('.')[-1]}")

cases = [
    ("member reference base type 's32' (aka 'long') is not a structure or union",
     "member-on-typed-pointer"),
    ("use of undeclared identifier 'RacePlayer'", "undeclared-identifier"),
    ("unknown type name 'PlayerCommandState'", "unknown-type-name"),
    ("implicit declaration of function 'foo'", "undeclared-function"),
    ("redeclaration of 'guMtxF2L'", "redeclaration/conflict"),
    ("incompatible pointer types assigning", "incompatible-pointer"),
    ("expected parameter declarator", "parameter-declarator"),
    ("something nobody has seen", "unclassified"),
]
bad = 0
for message, expected in cases:
    got = probe.classify_residual(message)
    mark = "ok " if got == expected else "BAD"
    bad += got != expected
    print(f"  {mark} {expected:24} <- {message[:52]}")
assert not bad, f"{bad} classifier mismatches"
print("\nclassifier ok; --trace-frontend wired:",
      "--trace-frontend" in Path(probe.__file__).read_text(encoding="utf-8"))
