"""Which steering residue blocks the counter-loop exact (exploration)."""
import json
import re
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import strength_inverse  # noqa: E402

import fire  # noqa: E402

name = sys.argv[1]
src, _ = fire.best_source(name)
comb = next(c for l, c in strength_inverse.counter_loop_variants(src, name) if l.endswith("+parallel"))
no_pair = re.sub(r"^[ \t]*i\+\+;\n[ \t]*i--;\n", "", comb, flags=re.M)
no_if = re.sub(r"^[ \t]*if \(\(1\)\) \{\n(?:[^\n]*\n)*?[ \t]*\}\n", "", comb, count=1, flags=re.M)
no_both = re.sub(r"^[ \t]*if \(\(1\)\) \{\n(?:[^\n]*\n)*?[ \t]*\}\n", "", no_pair, count=1, flags=re.M)
for label, cand in [("combined", comb), ("no_pair", no_pair), ("no_if", no_if), ("no_both", no_both)]:
    print(label, fire.build(name, cand)[0])
