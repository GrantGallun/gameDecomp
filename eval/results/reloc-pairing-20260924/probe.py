"""Exploration (before a protocol): which declaration form of an address-only global reproduces the target's HI16/LO16
relocation ORDER in osCreateMesgQueue? Bytes are already identical; only the .rel.text record order differs.
Compiles in the copied mining workspace, compares `objdump -r` of the candidate .o with target.o."""
import re, subprocess, sys
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp/eval/results/draft-reference-mining-20260924")
import pairs

PRE = '#include "include_asm.h"\n#include "compiler_diagnostics.h"\n#include <PR/mbi.h>\n'
BODY = """void osCreateMesgQueue(struct Q *arg0, s32 arg1, s32 arg2) {
    arg0->unk0 = {V};
    arg0->unk4 = {V};
    arg0->unkC = 0;
    arg0->unk10 = arg2;
    arg0->unk14 = arg1;
    arg0->unk8 = 0;
}
"""
Q = "struct Q { s32 unk0; s32 unk4; s32 unk8; s32 unkC; s32 unk10; s32 unk14; };\n"
QP = "struct Q { struct T *unk0; struct T *unk4; s32 unk8; s32 unkC; s32 unk10; s32 unk14; };\n"
VARIANTS = {
    "A byte array, decay":            (Q, "extern u8 __osThreadTail[8];", "(s32) __osThreadTail"),
    "B complete struct, &":           (Q, "struct T { s32 a; s32 b; }; extern struct T __osThreadTail;", "(s32) &__osThreadTail"),
    "C incomplete struct, &":         (Q, "struct T; extern struct T __osThreadTail;", "(s32) &__osThreadTail"),
    "D incomplete, pointer fields":   (QP, "struct T; extern struct T __osThreadTail;", "&__osThreadTail"),
    "E complete, pointer fields":     (QP, "struct T { s32 a; s32 b; }; extern struct T __osThreadTail;", "&__osThreadTail"),
    "F s32 scalar, &":                (Q, "extern s32 __osThreadTail;", "(s32) &__osThreadTail"),
}


def relocs(obj: Path) -> list[str]:
    out = subprocess.run(["mips-linux-gnu-objdump", "-r", str(obj)], capture_output=True, text=True).stdout
    return [" ".join(l.split()[:3]) for l in out.splitlines() if re.match(r"^[0-9a-f]{8} ", l)]


mirror = pairs.mirror_repo()
ws = pairs.workspace(mirror, "osCreateMesgQueue")
target = relocs(ws / "target.o")
print("target :", target)
for name, (q, decl, v) in VARIANTS.items():
    code = PRE + q + decl + "\n" + BODY.replace("{V}", v)
    b = pairs.build(ws, "relprobe", code)
    r = relocs(ws / "relprobe.o") if b["compiled"] else None
    print(f"{name:32s} score={b.get('score')} relocs={'SAME' if r == target else r}")


def context_test(name="osCreateMesgQueue"):
    """Harness check: does the REFERENCE definition reproduce the target's relocation order (a) in its own TU (every
    other definition reduced to a prototype) and (b) standalone behind the clean prelude? And our candidate (c) in the
    TU, (d) standalone. The reference is used only to test the harness, as pairs.py does."""
    import sqlite3
    root = pairs.compile_root(name)
    source = pairs.expanded(root)
    ref_tu, ref_def = pairs.tu_candidate(source, name, None)
    kb = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    cand = kb.execute("select a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                      "and a.run_id like 'binary-types%' order by a.id limit 1", (name,)).fetchone()[0]
    cand_def = [r for r in pairs.n64_corpus.extract_functions(cand) if r["name"] == name][0]["definition"]
    head = cand[:cand.find(cand_def)]
    arms = {"(a) reference in TU": ref_tu,
            "(b) reference standalone": head + ref_def + "\n",
            "(c) candidate in TU": pairs.tu_candidate(source, name, str(cand_def))[0],
            "(d) candidate standalone": cand}
    for label, code in arms.items():
        b = pairs.build(ws, "relctx", code)
        r = relocs(ws / "relctx.o") if b["compiled"] else None
        print(f"{label:28s} compiled={b['compiled']} score={b.get('score')} relocs={'SAME' if r == target else r}")


if len(sys.argv) > 1 and sys.argv[1] == "context":
    context_test(*(sys.argv[2:3] or []))
