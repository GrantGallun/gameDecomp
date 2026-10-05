"""Fire test of strength_inverse.counter_variants on real residuals (each function's round-3 best source)."""
import json
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import strength_inverse  # noqa: E402

import fire  # noqa: E402

for name in sys.argv[1:]:
    src, _ = fire.best_source(name)
    for label, cand in strength_inverse.counter_loop_variants(src, name):
        score, dump, log = fire.build(name, cand)
        print(f"{name:45s} {label:28s} {score}", log[-200:] if score is None else "")
