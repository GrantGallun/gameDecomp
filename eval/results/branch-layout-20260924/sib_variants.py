"""Fire variants for the 99.936 siblings (gap 3 exploration): the second loop counted by the reused `i`."""
import json
import re
import sys

import fire

name = sys.argv[1]
src, _ = fire.best_source(name)
begin = src.index(name + "(")
body_start = src.index("{", begin)
head = src[:body_start + 1]
draw = re.findall(r"drawMenuSpriteTileClipped\((?:[^;]*?)\);", src[body_start:], re.S)
assert len(draw) == 4, len(draw)
first, right, bottom, corner = draw


def sub(call, table, idx, off=None):
    call = re.sub(rf"\.{table}\[\w+\]", f".{table}[{idx}]", call)
    if off is not None:
        call = call.replace("+ offset", f"+ {off}")
    return call


def body(first_idx, second_hdr, idx2, off, decls="    s32 i;\n"):
    return (head + "\n" + decls + "\n"
            + f"    for (i = 0; i < 16; i++) {{\n        {sub(first, 'center', first_idx)}\n    }}\n"
            + f"    {second_hdr} {{\n        {sub(right, 'right', idx2, off)}\n        {sub(bottom, 'bottom', idx2, off)}\n    }}\n"
            + f"    {corner}\n}}\n")


V = {
    "i_shift": body("i", "for (i = 0; i < 2; i++)", "i", "(i << 6)"),
    "i_mul": body("i", "for (i = 0; i < 2; i++)", "i", "(i * 0x40)"),
    "offset_loop_i_index": body("i", "for (i = 0; i < 0x80; i += 0x40)", "i / 0x40", "i"),
}
json.dump({k: [[src, v]] for k, v in V.items()}, open(f"fire_sib_{name}.json", "w"), indent=1)
print(V["i_shift"][len(head):])
