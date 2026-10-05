"""Build reviewed/ for 20261001-preparer-names: the frozen files plus ONLY this amendment's changes.

Three files are taken whole from main, in the frozen file's own line endings, after checking that frozen and main
differ in them by exactly this amendment's hunks (checked below by line-set difference, not trusted):

  eval/prepare_integration.py   candidate-local brace typedefs, object-like #define aliases and plain
                                `const char NAME[N] = "literal";` objects are carried to the destination TU
  eval/operand_repair.py        relocation_names.variants in the proposal list (literal_names filtered out)
  solver/relocation_names.py    field_names (struct field -> the target's separate global)

`solver/repair_queue.py` is the frozen file plus ONE hunk: the operand-repair digest also covers relocation_names.py, so
existing nodes are revisited once with the new proposals. Main's repair_queue carries unrelated, unreviewed edits and is
not copied.

    python3 build_reviewed.py      # writes reviewed/ and prints the hashes stage.py pins
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
MAIN = HERE.parents[4]

FROM_MAIN = ('eval/prepare_integration.py', 'eval/operand_repair.py', 'solver/relocation_names.py')
# Frozen lines main replaces (the only ones allowed to disappear), by file: anything else removed means unrelated drift.
EXPECTED_REPLACED = {
    'eval/prepare_integration.py': 10,
    'eval/operand_repair.py': 1,
    'solver/relocation_names.py': 1,
}
DIGEST_OLD = "                     'solver/file_scope_objects.py', 'solver/object_discrepancy.py'):"
DIGEST_NEW = ("                     'solver/file_scope_objects.py', 'solver/object_discrepancy.py',\n"
              "                     'solver/relocation_names.py'):")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fold(data: bytes) -> list[str]:
    return data.replace(b'\r\n', b'\n').decode('utf-8').split('\n')


def main() -> None:
    out = {}
    for rel in FROM_MAIN:
        frozen = (FROZEN / rel).read_bytes()
        main_bytes = (MAIN / rel).read_bytes()
        removed = [line for line in fold(frozen) if line not in set(fold(main_bytes))]
        if len(removed) != EXPECTED_REPLACED[rel]:
            raise SystemExit(f'{rel}: frozen has {len(removed)} lines main lacks, expected {EXPECTED_REPLACED[rel]}: '
                             f'{removed[:3]}')
        crlf = frozen.count(b'\r\n') > frozen.count(b'\n') // 2
        text = main_bytes.replace(b'\r\n', b'\n')
        data = text.replace(b'\n', b'\r\n') if crlf else text
        target = HERE / 'reviewed' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out[rel] = {'frozen': sha(frozen), 'reviewed': sha(data)}
    rel = 'solver/repair_queue.py'
    frozen = (FROZEN / rel).read_bytes()
    crlf = frozen.count(b'\r\n') > (frozen.count(b'\n') - frozen.count(b'\r\n'))
    for eol in ((b'\r\n', b'\n') if crlf else (b'\n', b'\r\n')):
        old, new = DIGEST_OLD.encode() + eol, DIGEST_NEW.encode().replace(b'\n', eol) + eol
        if frozen.count(old) == 1:
            data = frozen.replace(old, new)
            break
    else:
        raise SystemExit('repair_queue digest anchor not found exactly once')
    target = HERE / 'reviewed' / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    out[rel] = {'frozen': sha(frozen), 'reviewed': sha(data)}
    for rel in FROM_MAIN:
        a = (HERE / 'reviewed' / rel).read_bytes().replace(b'\r\n', b'\n')
        b = (MAIN / rel).read_bytes().replace(b'\r\n', b'\n')
        if a != b:
            raise SystemExit(f'{rel}: reviewed differs from main beyond line endings')
    for rel, row in out.items():
        print(f"{rel}: frozen {row['frozen']} reviewed {row['reviewed']}")


if __name__ == '__main__':
    main()
