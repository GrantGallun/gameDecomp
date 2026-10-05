"""Dry-run solver.stack_layout on stored diffs (no compile): python3 stack_dry.py FUNCTION..."""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import stack_layout  # noqa: E402

for name in sys.argv[1:]:
    entry = json.loads((HERE / "diffs" / f"{name}.json").read_text())
    deltas = stack_layout.stack_deltas(entry["diff"])
    decls, _b, _e = stack_layout._declarations(entry["source_text"], name)
    found = list(stack_layout.variants(entry["source_text"], name, entry["diff"]))
    print(name, deltas, "decls", [d.group("name") for _s, _t, d in decls], "variants", len(found),
          dict(Counter(k for _l, k, _v in found)))
    for label, _kind, _text in found[:6]:
        print("   ", label)
