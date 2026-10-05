import json
import re
from measure import OUT, run
from solver import rewrites


def candidates():
    for name, parent in [("Fvibup", 25), ("Fvibdown", 28)]:
        source = (OUT / f"{name}--{parent}.c").read_text()
        load = re.search(r"    temp_t8 = (.+);\n", source)
        code = source[:load.start()] + source[load.end():]
        code = code.replace("    var_f6 = (f32) temp_t8;\n", "")
        code = re.sub(r"\bvar_f6\b(?!;)", "((f32) (u32) " + load[1] + ")", code)
        yield name, "inline-both-conversion-temporaries", code, parent
    name, parent = "releaseMenuAssetHandles", 14
    source = (OUT / f"{name}--{parent}.c").read_text()
    diff = json.loads((OUT / f"{name}--{parent}.json").read_text())["diff"]
    for rewrite in rewrites.branch_sentinel_rewrites(source, diff):
        yield name, rewrite.label, rewrite(source), parent


if __name__ == "__main__":
    run("probe4", candidates())
