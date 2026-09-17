"""Functions whose ROM-backed FUNCTION certificate says exact while the object certificate says not.

Discovered on `osSpTaskStartGo`: `normalized_assembly_exact: true`, `exact: false`,
`status: object_sections_differ`, and `.text` sizes 80 (target) vs 64 (candidate) -- but
`function_boundary.function_exact: true`, which is a ROM-backed byte comparison of the function's own
extent plus its external call relocations.

So the section verdict is failing on EXTENT while the function bytes match. That is either a class of
real matches the pipeline never counted, or a class where the function certificate is too permissive --
and the only way to tell is to count it and read the evidence, not to guess.

    python3 eval/function_certificate_census.py [--repo ~/decomp/sbk1] [--out FILE]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp" / "sbk1")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    rows = []
    for path in (args.repo / "nonmatchings").glob("*/*.verification.json"):
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if d.get("kind") != "mips_object_section_certificate":
            continue
        boundary = d.get("function_boundary") or {}
        rows.append({
            "function": path.parent.name,
            "file": path.name,
            "exact": bool(d.get("exact")),
            "status": d.get("status"),
            "normalized_assembly_exact": bool(d.get("normalized_assembly_exact")),
            "function_exact": bool(boundary.get("function_exact")),
            "target_text": (d.get("target_image") or {}).get("sections", {}).get(".text", {}).get("size"),
            "candidate_text": (d.get("candidate_image") or {}).get("sections", {}).get(".text", {}).get("size"),
            "relocation_order_equivalent": d.get("relocation_order_equivalent"),
        })
    print(f"certificates scanned: {len(rows)}")
    functions = {r["function"] for r in rows}
    print(f"distinct functions: {len(functions)}")
    print("by status:", dict(collections.Counter(r["status"] for r in rows)))

    interesting = [r for r in rows if not r["exact"] and r["function_exact"]]
    print(f"\n=== function_exact TRUE while exact FALSE: {len(interesting)} certificates "
          f"across {len({r['function'] for r in interesting})} functions ===")
    by_fn: dict[str, dict] = {}
    for r in interesting:
        by_fn.setdefault(r["function"], r)
    for name, r in sorted(by_fn.items()):
        print(f"  {name:<44} status={r['status']:<24} norm_asm={r['normalized_assembly_exact']} "
              f"text {r['target_text']}->{r['candidate_text']} reloc_eq={r['relocation_order_equivalent']}")

    hunkless = [r for r in rows if not r["exact"] and r["normalized_assembly_exact"]]
    print(f"\n=== normalized_assembly_exact TRUE while exact FALSE: {len(hunkless)} certificates "
          f"across {len({r['function'] for r in hunkless})} functions ===")
    for name in sorted({r["function"] for r in hunkless})[:20]:
        r = next(x for x in hunkless if x["function"] == name)
        print(f"  {name:<44} status={r['status']:<24} function_exact={r['function_exact']} "
              f"text {r['target_text']}->{r['candidate_text']}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"rows": rows, "function_exact_only": sorted(by_fn)}, indent=2),
                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
