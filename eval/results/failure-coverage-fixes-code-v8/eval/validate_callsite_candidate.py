"""Validate one compiled parent candidate against exact-leaf callsite facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from solver.callsite_contracts import validate_candidate_asm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", required=True, type=Path,
                        help="callsite_contract_pilot JSON receipt")
    parser.add_argument("--function", required=True)
    parser.add_argument("--candidate-asm", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    receipt = json.loads(args.contracts.read_text(encoding="utf-8"))
    row = next((item for item in receipt.get("rows", [])
                if item.get("function") == args.function), None)
    if row is None:
        raise SystemExit(f"function not found in contract receipt: {args.function}")
    assembly = args.candidate_asm.read_text(encoding="utf-8")
    start = assembly.find(f"glabel {args.function}")
    if start != -1:
        end = assembly.find(f"endlabel {args.function}", start)
        assembly = assembly[start:end if end != -1 else None]
    result = validate_candidate_asm(row["contracts"], assembly)
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"receipt: {args.out}")
    print(text, end="")


if __name__ == "__main__":
    main()
