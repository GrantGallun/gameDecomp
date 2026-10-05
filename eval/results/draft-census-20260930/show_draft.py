import sys
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from pathlib import Path
from solver import m2c_input
REPO = Path("/home/grant/decomp/sbk1")
for fn in sys.argv[1:]:
    r, _ = m2c_input.draft(REPO, REPO / "nonmatchings" / fn / "target.s")
    print("=====", fn); print(r.stdout[-2500:])
