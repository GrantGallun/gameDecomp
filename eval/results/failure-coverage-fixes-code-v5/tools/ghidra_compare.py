"""Compare two Ghidra receipts by BSim overlap and semantic program shape."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from solver import ghidra_context, ghidra_similarity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args()
    left = ghidra_context.load(args.left)
    right = ghidra_context.load(args.right)
    print(json.dumps(ghidra_similarity.compare(left, right), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
