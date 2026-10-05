import sys
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from pathlib import Path
from solver import m2c_input, loop_shape
REPO = Path("/home/grant/decomp/sbk1")
for fn in sys.argv[1:]:
    r, _ = m2c_input.draft(REPO, REPO / "nonmatchings" / fn / "target.s")
    src = r.stdout
    print("=====", fn)
    for label, new in loop_shape.variants(src, fn):
        print("---", label)
        print(new[new.index(fn):][:1400])
