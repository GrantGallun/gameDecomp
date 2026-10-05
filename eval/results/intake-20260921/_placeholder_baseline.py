"""Is the failing compile_recovery test pre-existing, or caused by the widened placeholder pattern?

`tests/test_compile_recovery.py::test_compile_recovery_reapplies_absolute_adapter_to_later_drafts` expects
`compile_recovery.variants` to offer a `('m2c-type-placeholder', ...)` row. It does not. The suspicion is
the widened `DECL_LINE`, so this neutralises ONLY that widening -- restoring the old line-anchored,
star-region spelling -- runs the test, and restores the file whatever happens.

The module is untracked in git, so `git stash` cannot be used to get the old version; rewriting the one
pattern and putting it back is the only available baseline.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MODULE = Path("/mnt/c/Code/gameDecomp/solver/m2c_placeholders.py")
ORIGINAL = MODULE.read_text(encoding="utf-8")
BACKUP = Path("/tmp/m2c_placeholders.original.py")
BACKUP.write_text(ORIGINAL, encoding="utf-8")

start = ORIGINAL.index("DECL_LINE = re.compile(")
end = ORIGINAL.index("PARAM = re.compile(")
OLD_PATTERN = (
    'DECL_LINE = re.compile(\n'
    '    r"^(?P<indent>\\s*)(?P<extern>extern\\s+)?\\?(?P<stars>\\s*\\*+)?\\s*'
    '(?P<name>[A-Za-z_]\\w*)", re.M)\n'
)
NEUTRALISED = ORIGINAL[:start] + OLD_PATTERN + ORIGINAL[end:]

print("before: the widened pattern is in place")
print(f"  {ORIGINAL[start:start + 130].splitlines()[1].strip()[:100]}")
print(f"after : the old pattern")
print(f"  {OLD_PATTERN.splitlines()[1].strip()[:100]}")

run = lambda: subprocess.run(
    [sys.executable, "-m", "pytest",
     "tests/test_compile_recovery.py::test_compile_recovery_reapplies_absolute_adapter_to_later_drafts",
     "-q"], cwd="/mnt/c/Code/gameDecomp", capture_output=True, text=True,
    env={"PYTHONPATH": "/mnt/c/Code/gameDecomp", "PATH": "/usr/bin:/bin"})

try:
    MODULE.write_text(NEUTRALISED, encoding="utf-8")
    result = run()
    print(f"\nwith the OLD pattern  : {'PASSED' if result.returncode == 0 else 'FAILED'}")
    print("  " + result.stdout.strip().splitlines()[-1][:110])
finally:
    MODULE.write_text(ORIGINAL, encoding="utf-8")

assert MODULE.read_text(encoding="utf-8") == ORIGINAL, "the module was not restored"
result = run()
print(f"with the NEW pattern  : {'PASSED' if result.returncode == 0 else 'FAILED'}")
print("  " + result.stdout.strip().splitlines()[-1][:110])
print("\nmodule restored byte-identical")
