"""Compile synthetic C on IDO 5.3 with the game's own -O2 recipe (or an -O override) and return instructions."""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import allocator_interventions as ai  # noqa: E402

TREE = Path.home() / "decomp/tools-src/sbk1-trace-gate"
CC = Path.home() / "decomp/tools-src/ido-trace/cc"


def flags(opt=None):
    f = ai.cc_flags(ai.recipe(TREE))
    if opt:
        f = [opt if a.startswith("-O") else a for a in f]
    return f


def compile_(src, opt=None, function=None):
    probe = TREE / "build" / "syn_branch"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "b.c").write_text(src)
    r = subprocess.run([str(CC), *flags(opt), "-o", str(probe / "b.o"), str(probe / "b.c")], cwd=TREE,
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-400:])
    d = subprocess.run(["mips-linux-gnu-objdump", "-d", "--no-show-raw-insn", str(probe / "b.o")],
                       capture_output=True, text=True).stdout
    out, cur = {}, None
    for line in d.splitlines():
        m = re.match(r"^[0-9a-f]+ <(\w+)>:", line)
        if m:
            cur = out.setdefault(m.group(1), [])
        elif cur is not None and re.match(r"^\s+[0-9a-f]+:", line):
            text = line.split(":", 1)[1].strip().replace("\t", " ")
            text = re.sub(r" <[^>]*>", "", text)
            cur.append(text)
    for insns in out.values():
        while insns and insns[-1] == "nop":
            insns.pop()
    return out[function] if function else out


def branch_shape(insns):
    """Compact control-flow signature: ops with branch targets as relative instruction deltas."""
    sig = []
    for i, ins in enumerate(insns):
        op, _, rest = ins.partition(" ")
        if op in ("b", "j") or (op.startswith("b") and op not in ("break",)):
            try:
                t = int(rest.split(",")[-1], 16) // 4
                sig.append(f"{i}:{op}->{t}")
            except ValueError:
                pass
        elif op == "jr":
            sig.append(f"{i}:jr {rest}")
    return sig
