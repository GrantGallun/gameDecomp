import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from constructs import HDR, BASE_LOCALS, BASE_BODY, VARIANTS, TREE  # noqa: E402

for name in sys.argv[1:]:
    locals_, body, _ = VARIANTS[name]
    src = HDR + "void syn_f(int n) {\n" + BASE_LOCALS + locals_ + BASE_BODY + body + "}\n"
    probe = TREE / "build" / "syn_probe"
    (probe / "fr.c").write_text(src)
    subprocess.run([str(Path.home() / "decomp/tools-src/ido-trace/cc"), *ai.cc_flags(ai.recipe(TREE)), "-o",
                    str(probe / "fr.o"), str(probe / "fr.c")], cwd=TREE, check=True, capture_output=True)
    d = subprocess.run(["mips-linux-gnu-objdump", "-d", "--no-show-raw-insn", str(probe / "fr.o")],
                       capture_output=True, text=True).stdout
    print("==", name)
    print("  " + " | ".join(l.split(":", 1)[1].strip().replace("\t", " ") for l in d.splitlines()
                           if re.match(r"^\s+[0-9a-f]+:", l) and "sp" in l))
