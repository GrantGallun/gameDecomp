"""osMotorStart at 99.223 (registers only): try the loop's C shape variants, one compile each."""
import sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace, residual_classes as rc
REPO = Path("/home/grant/decomp/sbk1")
conn = sqlite3.connect("/home/grant/decomp/runs/unaligned-copy-20260929/trial.sqlite")
fn = sys.argv[1] if len(sys.argv) > 1 else "osMotorStart"
a = conn.execute("select addr from functions where name=?", (fn,)).fetchone()[0]
src = conn.execute("select source_code from attempts where func_addr=? and strategy like ? order by id desc limit 1",
                   (a, "copy-finish:temp_copyback:temp_t4%")).fetchone()[0]
old = """    if (arg0->channel != 0) {
        sp4C = 0;
        if (arg0->channel > 0) {
            for (;;) {
                sp4C = sp4C + 1;
                sp44 += 1;
            
                if (!(sp4C < arg0->channel)) break;
            }
        }
    }"""
assert old in src, "loop text not found"
shapes = {
    "swap": old.replace("                sp4C = sp4C + 1;\n                sp44 += 1;", "                sp44 += 1;\n                sp4C = sp4C + 1;"),
    "for": """    if (arg0->channel != 0) {
        for (sp4C = 0; sp4C < arg0->channel; sp4C++) {
            sp44++;
        }
    }""",
    "for_plain": """    for (sp4C = 0; sp4C < arg0->channel; sp4C++) {
        sp44++;
    }""",
    "for_ptr_first": """    if (arg0->channel != 0) {
        for (sp4C = 0; sp4C < arg0->channel; sp4C++, sp44++) {
        }
    }""",
    "postinc": old.replace("sp4C = sp4C + 1;", "sp4C++;").replace("sp44 += 1;", "sp44++;"),
}
ws = workspace.bootstrap(REPO, fn); run_id = f"loopshape-{int(time.time())}-{fn}"
for name, text in shapes.items():
    at = workspace.score(ws, REPO, f"{fn}_loopshape_{time.time_ns()}", src.replace(old, text), conn=conn, func=fn,
                         strategy=f"loopshape:{name}", run_id=run_id, run_kind="loopshape")
    conn.commit()
    print(f"{name:14s} compiled={at.compiled} score={at.score} exact={workspace.repair_complete(at)} "
          f"{[rc.counts(at.diff)[c] for c in rc.CLASSES] if at.compiled else (at.compiler_stderr or '')[-150:]}")
