"""Among calling functions with no memory locals: does the +8 residual track stack-passed arguments (sw x,16(sp)+)?"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval.allocator_rules import procedures  # noqa: E402
from eval.frame_size import facts  # noqa: E402

REPO = Path.home() / "decomp/sbk1"
STACK_ARG = re.compile(r"^s[bhw]c?1?\s+\$?\w+,(0x1[0-9a-f]|1[6-9]|2\d|3\d)\(sp\)", re.M)
table = collections.Counter()
for proc in procedures(Path.home() / "decomp/tools-src/uopt-trace-census/traces"):
    dump = REPO / "nonmatchings" / proc.name / "target_object_dump_normalized.s"
    if not dump.exists():
        continue
    text = dump.read_text(errors="replace")
    f = facts(text, proc)
    if not f["calls"] or f["memory_locals"]:
        continue
    # outgoing slots actually written below the saved-register area
    body = "\n".join(l.strip() for l in text.splitlines())
    slots = [int(m.group(1), 0) for m in STACK_ARG.finditer(body)]
    save_floor = f["frame"] - 4 * f["saved_int"] - 8 * f["saved_float"]
    outgoing = [s for s in slots if s < save_floor]
    table[(f["frame"] - f["predicted"], bool(outgoing))] += 1
print("(residual, writes a stack argument slot) -> functions")
for k, v in sorted(table.items()):
    print(" ", k, v)
