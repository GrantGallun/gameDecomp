"""Build reviewed/ = the frozen file plus ONLY the object-layer hunks, byte-for-byte otherwise.

Main carries unrelated, unreviewed edits in residual.py (instruction/register distances) and repair_queue.py
(investigation policy), so no whole main file is copied except where frozen and main differ by exactly these hunks
(checked below). Each inserted line takes the line ending of its anchor, so mixed-ending frozen files stay as they are.

    python3 build_reviewed.py      # writes reviewed/ and prints the hashes stage.py pins
"""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
MAIN = HERE.parents[4]

# (old, new) in LF form; `old` must occur exactly once in the frozen file.
HUNKS = {
    'solver/workspace.py': [
        ('    frontend: dict | None = None\n    source_attribution: dict | None = None\n',
         '    frontend: dict | None = None\n    source_attribution: dict | None = None\n'
         '    # solver.object_discrepancy.summarize: what differs outside the text diff, and the route it implies.\n'
         '    object: dict | None = None\n'),
        ("    # Failures are logged too: the non-compiling rows are exactly what made\n"
         "    # today's extraction bugs findable.\n    att.compiler_recipe = recipe\n",
         "    # Every compiled attempt, not only certified ones: the normalized diff cannot show data sections,\n"
         "    # relocation spelling or trailing extent, and a residual it hides looks exactly like \"nothing to do\"\n"
         "    # (eval/results/hidden-object-20260930). Two small ELF parses; a failure here never fails the score.\n"
         "    if m and (ws / \"target.o\").is_file() and (ws / f\"{name}.o\").is_file():\n"
         "        try:\n"
         "            from solver import object_discrepancy\n"
         "            att.object = object_discrepancy.summarize((ws / \"target.o\").read_bytes(),\n"
         "                                                      (ws / f\"{name}.o\").read_bytes(), code, func)\n"
         "            if att.exact:                                         # the certificate's second stages outrank raw rows\n"
         "                att.object[\"route\"] = \"exact\"\n"
         "            log_kw[\"extra\"] = {**(log_kw.get(\"extra\") or {}), \"object\": att.object}\n"
         "        except Exception as exc:                                  # diagnostic only, never a verdict\n"
         "            att.object = {\"route\": \"error\", \"error\": f\"{type(exc).__name__}: {exc}\"[:300]}\n"
         "    # Failures are logged too: the non-compiling rows are exactly what made\n"
         "    # today's extraction bugs findable.\n    att.compiler_recipe = recipe\n"),
    ],
}
# Pure insertions after a unique anchor LINE, each inserted line ending like the anchor line. Frozen residual.py mixes
# LF and CRLF inside these very anchors, so a replace-based hunk would rewrite bytes it has no business touching.
INSERTS = {
    'solver/residual.py': [
        ('    frontend: dict | None = None',
         ['    # Object rows the diff cannot show and their route (solver.object_discrepancy.classify).',
          '    object: dict | None = None']),
        ('        frontend=attempt.frontend,',
         ['        object=getattr(attempt, "object", None),']),
    ],
}
HUNKS |= {
    'solver/repair_queue.py': [
        ("        for name in ('byte_certificate.py', 'function_boundary.py'):",
         "        # object_discrepancy routes the verdict (2026-09-30): a change re-certifies once, which also gives\n"
         "        # retained packets their `object` route -- without it a zero-fault node is never re-scored.\n"
         "        for name in ('byte_certificate.py', 'function_boundary.py', 'object_discrepancy.py'):"),
        ("                     'solver/workspace.py', 'solver/family_gates.py', 'solver/edit_locality.py'):",
         "                     'solver/workspace.py', 'solver/family_gates.py', 'solver/edit_locality.py',\n"
         "                     'solver/file_scope_objects.py', 'solver/object_discrepancy.py'):"),
        ('def operand_profile(node):\n    """One full-evidence repair visit on a measured small structural residual."""\n'
         "    residual = node.get('residual') or {}\n    faults = residual.get('faults')\n"
         "    if (node.get('status') != 'pending' or not node.get('source')\n"
         "            or not node.get('source_sha256') or residual.get('compiled') is not True\n"
         "            or (residual.get('frontend') or {}).get('passed') is not True\n"
         "            or not isinstance(faults, dict) or not any(faults.values())\n"
         "            or faults.get('structural', 0) > 2):\n",
         'def object_route(node):\n'
         '    """The residual\'s object route (solver.object_discrepancy.classify), or None for packets that predate it."""\n'
         "    return ((node.get('residual') or {}).get('object') or {}).get('route')\n\n\n"
         'def operand_profile(node):\n'
         '    """One full-evidence repair visit on a measured small structural residual, or on an object lever.\n\n'
         '    A lever the object rows name (object_route) is admitted even with an all-zero fault vector: the diff cannot show\n'
         '    those residuals, so gating on diff faults alone declined exactly the nodes it owns (guMtxIdent, 2026-09-30)."""\n'
         "    residual = node.get('residual') or {}\n    faults = residual.get('faults')\n"
         "    lever = object_route(node) == 'generator'\n"
         "    if (node.get('status') != 'pending' or not node.get('source')\n"
         "            or not node.get('source_sha256') or residual.get('compiled') is not True\n"
         "            or (residual.get('frontend') or {}).get('passed') is not True\n"
         "            or (not lever and (not isinstance(faults, dict) or not any(faults.values())\n"
         "                               or faults.get('structural', 0) > 2))):\n"),
        ("    phase = lane(node)\n    if phase in {Lane.DONE, Lane.BLOCKED}:\n        return None\n    key = evidence_key(node)\n",
         "    phase = lane(node)\n    if phase in {Lane.DONE, Lane.BLOCKED}:\n        return None\n"
         "    if phase in {Lane.BYTE, Lane.ENVIRONMENT} and object_route(node) == 'certify':\n"
         "        # .text is byte-identical and every remaining row is a certificate/integration artifact\n"
         "        # (object_discrepancy.EXPLAINED): no C edit can move it. func_8005905C and func_8005C14C absorbed\n"
         "        # ~1,060 campaign attempts this way while their .text already matched (2026-09-30).\n"
         "        return None\n    key = evidence_key(node)\n"),
        ("    operand = operand_profile(node)\n    site = site_edit_profile(node)\n",
         "    operand = operand_profile(node)\n"
         "    if operand is not None and object_route(node) == 'generator' and phase in {Lane.BYTE, Lane.ENVIRONMENT}:\n"
         "        # A confirmed object lever names its own fix; take that one visit before any search.\n"
         "        return {**operand, 'lane': phase.value, 'evidence_key': key}\n"
         "    site = site_edit_profile(node)\n"),
    ],
}
# Frozen == main except exactly the object-layer hunks: reviewed = main, in frozen's line endings.
FROM_MAIN = ('eval/operand_repair.py', 'solver/rodata_symbol.py')
NEW = ('solver/object_discrepancy.py', 'solver/file_scope_objects.py')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch(data: bytes, old: str, new: str) -> bytes:
    # The file's dominant ending first: a single-line anchor matches under either, and its new lines must not
    # introduce the other ending.
    crlf = data.count(b'\r\n') > (data.count(b'\n') - data.count(b'\r\n'))
    for eol in ((b'\r\n', b'\n') if crlf else (b'\n', b'\r\n')):
        o, n = old.encode().replace(b'\n', eol), new.encode().replace(b'\n', eol)
        if data.count(o) == 1:
            return data.replace(o, n)
    raise SystemExit(f'anchor not found exactly once: {old[:60]!r}')


def insert(data: bytes, anchor: str, lines: list[str]) -> bytes:
    import re
    found = list(re.finditer(re.escape(anchor.encode()) + rb'(\r?\n)', data))
    if len(found) != 1:
        raise SystemExit(f'insert anchor not found exactly once: {anchor!r}')
    m = found[0]
    eol = m.group(1)
    return data[:m.end()] + b''.join(line.encode() + eol for line in lines) + data[m.end():]


def main() -> None:
    out = {}
    for rel, inserts in INSERTS.items():
        data = (FROZEN / rel).read_bytes()
        frozen_sha = sha(data)
        for anchor, lines in inserts:
            data = insert(data, anchor, lines)
        target = HERE / 'reviewed' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out[rel] = {'frozen': frozen_sha, 'reviewed': sha(data)}
    for rel, hunks in HUNKS.items():
        data = (FROZEN / rel).read_bytes()
        frozen_sha = sha(data)
        for old, new in hunks:
            data = patch(data, old, new)
        target = HERE / 'reviewed' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out[rel] = {'frozen': frozen_sha, 'reviewed': sha(data)}
    for rel in FROM_MAIN:
        frozen = (FROZEN / rel).read_bytes()
        crlf = frozen.count(b'\r\n') > frozen.count(b'\n') // 2
        main = (MAIN / rel).read_bytes().replace(b'\r\n', b'\n')
        data = main.replace(b'\n', b'\r\n') if crlf else main
        target = HERE / 'reviewed' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out[rel] = {'frozen': sha(frozen), 'reviewed': sha(data)}
    # workspace.py: the patched frozen copy must equal main (LF-folded) -- the only main change is this layer.
    for rel in ('solver/workspace.py',) + FROM_MAIN:
        a = (HERE / 'reviewed' / rel).read_bytes().replace(b'\r\n', b'\n')
        b = (MAIN / rel).read_bytes().replace(b'\r\n', b'\n')
        if a != b:
            raise SystemExit(f'{rel}: reviewed differs from main beyond line endings')
    for rel in NEW:
        target = HERE / 'reviewed' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((MAIN / rel).read_bytes().replace(b'\r\n', b'\n'))
        out[rel] = {'frozen': None, 'reviewed': sha(target.read_bytes())}
    for rel, row in out.items():
        print(f"{rel}: frozen {row['frozen']} reviewed {row['reviewed']}")


if __name__ == '__main__':
    main()
