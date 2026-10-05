"""Trace `header_variant` on the case my exclusion broke, step by step.

The test is right and my fix did not address it: `f` is DEFENDED here and the test still expects `PR/f.h`
to be added. So the excluded name is not `f`, and the earlier reasoning was a guess. This reproduces the
test's fixture and prints each set the function computes.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import compile_recovery, project_headers                      # noqa: E402

TMP = Path("/tmp/header-variant-trace")
(TMP / "include" / "game").mkdir(parents=True, exist_ok=True)
(TMP / "include" / "PR").mkdir(parents=True, exist_ok=True)
(TMP / "include" / "PRinternal").mkdir(parents=True, exist_ok=True)


def put(relative: str, text: str) -> None:
    (TMP / "include" / relative).write_text(text, encoding="utf-8")


put("common.h", "")
put("game/shim.h", "void f(int x);\ntypedef struct Q {int a;} Q;")
put("PR/f.h", "void f(void);")
put("PRinternal/state.h", "typedef struct __State {int a;} __State;\nextern __State __states[];")
source = '#include "common.h"\n#include "game/shim.h"\nvoid f(void) { __State *s = __states; }'

changed, report = compile_recovery.header_variant(TMP, "f", "glabel f", source, "build/src/ultra/f.o")
print("changed source:")
for line in changed.splitlines():
    print(f"   {line}")
print("report:", {k: report[k] for k in ("added", "removed_headers", "reconciled")})

# Recompute the sets the function uses, in the same order it does.
sdk = True
INCLUDES = compile_recovery.INCLUDES
original = INCLUDES.findall(source)
retained = [inc for inc in original if not (sdk and inc.startswith("game/"))]
clean = INCLUDES.sub("", source)
seed = "".join(f'#include "{inc}"\n' for inc in retained) + clean
provided = set(project_headers._included_declarations(TMP, seed))
known = set()
for inc in retained:
    known.update(compile_recovery.buildtypes.type_names(TMP, "include/" + inc))
used = set(re.findall(r"\b([A-Za-z_]\w*)\s*\*", clean))
used |= set(re.findall(r"\b(?:g[A-Z]\w*|__\w+)\b", clean))
functions = ["f", *project_headers.called_functions("glabel f")]
used.update(functions)

print(f"\noriginal includes   : {original}")
print(f"retained            : {retained}")
print(f"provided            : {sorted(provided)}")
print(f"known               : {sorted(known)}")
print(f"used                : {sorted(used)}")
print(f"functions           : {functions}")
print(f"candidates (used - provided - known): {sorted(used - provided - known)}")

# What are the choices for each candidate, and why?
headers = sorted((TMP / "include").rglob("*.h"))
headers = [h for h in headers if not h.relative_to(TMP / "include").as_posix().startswith("game/")]
for name in sorted(used - provided - known):
    if name in functions:
        choices = [d.include for d in project_headers.declarations(TMP, name)
                   if not d.include.startswith("game/")]
        print(f"\n{name}: a FUNCTION, declarations() -> {choices}")
    else:
        choices = []
        for header in headers:
            text = header.read_text(errors="replace")
            if name not in text:
                continue
            declared = any(project_headers._declared_name(d) == name
                           for _, _, d in project_headers._top_level_declarations(text))
            alias = re.search(r'(?m)^\s*typedef\s+(?:struct\s+|union\s+)?[A-Za-z_]\w*\s+'
                              + re.escape(name) + r'\s*;', project_headers._mask_noncode(text))
            typedef = project_headers._typedef_definition(text, name)
            if declared or alias or typedef:
                choices.append(header.relative_to(TMP / "include").as_posix())
        print(f"\n{name}: not a function, header scan -> {choices}")
