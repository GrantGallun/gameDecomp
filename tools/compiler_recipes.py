"""Compile a standalone C unit (checked context + function) with one of several MIPS compilers, for training tasks.

    obj, messages = compile_unit(recipe, text, workdir, tag)
    RECIPES: ido53-O2 / ido53-O1 (SBK1's flags), kmc-O2 / kmc-O1 (KMC GCC 2.7.2, the N64 GCC that
    SBK2 is built with; flags from its Makefile), ido71-O2 (when the 7.1 recomp is built)

Why several compilers: a model that has seen how DIFFERENT compilers lower the same C learns what is invariant (the
C) apart from what is one compiler's habit, and SBK2 is a GCC target. Every task names its compiler in the prompt,
and IDO exams stay the gate (a multi-compiler arm is adopted only if it does not regress there).

Units are the context closures (context_tasks.py): preprocessed C89, no includes, so any C89 compiler accepts them
without the game's build tree. A recipe that fails on a unit is a refusal for that unit, counted by the caller.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

HOME = Path.home()
KMC_DIR = HOME / "decomp/sbk2/tools/gcc_kmc"
KMC_FLAGS = ["-mabi=32", "-mgp32", "-mfp32", "-mno-abicalls", "-nostdinc", "-fno-PIC", "-G", "0",
             "-Wa,-force-n64align", "-funsigned-char", "-w", "-mips3", "-EB", "-fno-builtin", "-fno-common"]
IDO71 = HOME / "decomp/tools-src/ido-static-recomp/build/7.1/out"
IDO53 = HOME / "decomp/sbk1/tools/ido-recomp/linux"
# SBK1's game flags (its Makefile CFLAGS), minus include paths: units are preprocessed closures.
IDO_FLAGS = ["-c", "-G", "0", "-non_shared", "-Xcpluscomm", "-nostdinc", "-Wab,-r4300_mul", "-woff",
             "649,838,712,516"]

RECIPES = {
    "ido53-O2": {"compiler": "IDO 5.3", "opt": "-O2 -mips1", "kind": "ido53", "flags": IDO_FLAGS + ["-O2", "-mips1"]},
    "ido53-O1": {"compiler": "IDO 5.3", "opt": "-O1 -mips2", "kind": "ido53", "flags": IDO_FLAGS + ["-O1", "-mips2"]},
    "kmc-O2": {"compiler": "KMC GCC 2.7.2", "opt": "-O2 -mips3", "kind": "kmc", "flags": KMC_FLAGS + ["-O2"]},
    "kmc-O1": {"compiler": "KMC GCC 2.7.2", "opt": "-O1 -mips3", "kind": "kmc", "flags": KMC_FLAGS + ["-O1"]},
    "ido71-O2": {"compiler": "IDO 7.1", "opt": "-O2 -mips2", "kind": "ido71", "flags": IDO_FLAGS + ["-O2", "-mips2"]},
}


def available(recipe: str) -> bool:
    kind = RECIPES[recipe]["kind"]
    if kind == "kmc":
        return (KMC_DIR / "gcc").exists()
    if kind == "ido71":
        return (IDO71 / "cc").exists()
    if kind == "ido53":
        return (IDO53 / "cc").exists()
    return False


def compile_unit(recipe: str, text: str, workdir: Path, tag: str,
                 extra: tuple[str, ...] = ()) -> tuple[Path | None, str]:
    """(object or None, compiler messages). The unit is written to `workdir`; nothing else is touched."""
    r = RECIPES[recipe]
    workdir.mkdir(parents=True, exist_ok=True)
    stem = f"{os.getpid()}_{threading.get_ident()}_{tag}"
    src, obj = workdir / f"{stem}.c", workdir / f"{stem}.o"
    src.write_text(text, encoding="utf-8")
    obj.unlink(missing_ok=True)
    if r["kind"] == "kmc":
        cmd, env = [str(KMC_DIR / "gcc"), "-c", *r["flags"], *extra, "-o", str(obj), str(src)], \
            os.environ | {"COMPILER_PATH": str(KMC_DIR)}
    else:
        cc = (IDO71 if r["kind"] == "ido71" else IDO53) / "cc"
        cmd, env = [str(cc), *r["flags"], *extra, "-o", str(obj), str(src)], os.environ
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env, cwd=workdir)
    except subprocess.TimeoutExpired:
        return None, "compile timed out"
    finally:
        src.unlink(missing_ok=True)
    msgs = (p.stderr or "").replace(str(src), "candidate.c")
    return (obj if p.returncode == 0 and obj.exists() else None), msgs
