"""Run the REAL `header_variant` with its internals printed, for both cases.

Two states have to hold at once and they pull in opposite directions:

    __allocParam  draft is `? *__allocParam(void) {` -- note the `?` RETURN TYPE. It does not call itself.
    f (SDK test)  draft is `void f(void) { ... }`. It does not call itself either.

So "does it call itself" cannot separate them, whoever is right about the sets. This instruments the real
function rather than reimplementing it, because two reimplementations have already disagreed with it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import compile_recovery, project_headers                       # noqa: E402

# --- case 1: the SDK fixture -------------------------------------------------
TMP = Path("/tmp/header-variant-trace")
sdk_source = '#include "common.h"\n#include "game/shim.h"\nvoid f(void) { __State *s = __states; }'

# --- case 2: the real draft --------------------------------------------------
DRAFT = Path.home() / "decomp/sbk1/nonmatchings/__allocParam/base.c"
draft_source = DRAFT.read_text(encoding="utf-8", errors="replace")


def instrument(repo: Path, function: str, asm: str, source: str, target: str) -> None:
    sdk = target.startswith("build/src/ultra/")
    original = compile_recovery.INCLUDES.findall(source)
    retained = [inc for inc in original if not (sdk and inc.startswith("game/"))]
    clean = compile_recovery.INCLUDES.sub("", source)
    seed = "".join(f'#include "{inc}"\n' for inc in retained) + clean
    provided = set(project_headers._included_declarations(repo, seed))
    known = set()
    for inc in retained:
        known.update(compile_recovery.buildtypes.type_names(repo, "include/" + inc))
    used = set(re.findall(r"\b([A-Za-z_]\w*)\s*\*", clean))
    used |= set(re.findall(r"\b(?:g[A-Z]\w*|__\w+)\b", clean))
    functions = [function, *project_headers.called_functions(asm)]
    used.update(functions)

    called = {m.group(1) for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", clean)}
    matches = list(re.finditer(
        r"(?m)^[ \t]*(?:[A-Za-z_]\w*[ \t*]+)+?(?P<name>[A-Za-z_]\w*)[ \t]*\([^;{]*\)[ \t]*\{", clean))
    defined = {m.group("name") for m in matches if m.group("name") in called}
    defined |= {m.group(1) for m in re.finditer(
        r"(?m)^[ \t]*typedef\s+(?:struct|union|enum)\s+[A-Za-z_]\w*\s+(?P<name>[A-Za-z_]\w*)\s*;", clean)}

    print(f"--- {function} (sdk={sdk}) ---")
    print(f"  clean            : {clean.splitlines()[0][:70] if clean.splitlines() else ''}")
    print(f"  called           : {sorted(called)}")
    print(f"  definition regex : {[(m.group('name'), m.group(0).strip()) for m in matches]}")
    print(f"  defined          : {sorted(defined)}")
    print(f"  provided         : {sorted(provided)}")
    print(f"  candidates       : {sorted(used - provided - known - defined)}")
    print(f"  choices for {function!r}: "
          f"{[d.include for d in project_headers.declarations(repo, function) if not sdk or not d.include.startswith('game/')]}")


instrument(TMP, "f", "glabel f", sdk_source, "build/src/ultra/f.o")
print()
instrument(Path.home() / "decomp/sbk1", "__allocParam", "glabel __allocParam", draft_source,
           "build/src/ultra/audio/synth.o")
