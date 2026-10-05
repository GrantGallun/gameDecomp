"""Control arm: this morning's site-edit search (no shape edits, strict family order, budget 48 in run_control_inner)."""
import multiprocessing, runpy, sys
multiprocessing.set_start_method("fork", force=True)   # workers inherit the patches below
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import site_edits
site_edits._shape_edits = lambda source, function, diff: []
site_edits._interleaved = lambda edits: list(edits)
sys.argv = [str(Path(__file__).with_name("run_control_inner.py"))] + sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
