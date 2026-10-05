"""Which names does `header_variant`'s `defined` set actually contain on the SDK fixture?

The test expects `PR/f.h` to be added; the report says it was not. The add loop skips `used - provided -
known - defined`, so `f` is in one of those sets. This reproduces the fixture and prints all five, using
the same patterns the function uses, rather than guessing which one.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")

from solver import compile_recovery, project_headers                       # noqa: E402

TMP = Path("/tmp/header-variant-trace")
source = '#include "common.h"\n#include "game/shim.h"\nvoid f(void) { __State *s = __states; }'

clean = compile_recovery.INCLUDES.sub("", source)
print(f"clean:\n{clean}")

used = set(re.findall(r'\b([A-Za-z_]\w*)\s*\*', clean))
used |= set(re.findall(r'\b(?:g[A-Z]\w*|__\w+)\b', clean))
functions = ["f", *project_headers.called_functions("glabel f")]
used.update(functions)

called = {match.group(1) for match in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", clean)}
print(f"\ncalled set from /([A-Za-z_]\\w*)\\s*\\(/ : {sorted(called)}")

definitions = list(re.finditer(
    r'(?m)^[ \t]*(?:[A-Za-z_]\w*[ \t*]+)+?(?P<name>[A-Za-z_]\w*)[ \t]*\([^;{]*\)[ \t]*\{', clean))
print(f"definition matches: {[(m.group('name'), m.group(0).strip()) for m in definitions]}")
defined = {m.group("name") for m in definitions if m.group("name") in called}
defined |= {m.group(1) for m in re.finditer(
    r'(?m)^[ \t]*typedef\s+(?:struct|union|enum)\s+[A-Za-z_]\w*\s+(?P<name>[A-Za-z_]\w*)\s*;', clean)}
print(f"defined set: {sorted(defined)}")

provided = set(project_headers._included_declarations(TMP, compile_recovery.INCLUDES.sub("", source)
                                                      if False else
                                                      '#include "common.h"\n' + clean))
print(f"provided set: {sorted(provided)}")
print(f"\nused - provided - known - defined = {sorted(used - provided - set() - defined)}")
print(f"'f' in used={('f' in used)} provided={('f' in provided)} defined={('f' in defined)}")
