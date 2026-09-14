"""Import the reference decomp's own types into a KB -- deliberately tainted.

ROADMAP Phase 1 asks for this: `symbol_addrs.txt` (2,983 entries) and ~170
headers already hold SBK1's type knowledge, and none of it is queryable. But
CLAUDE.md forbids exactly this flow, and it is right to: `eval/ground_truth.py`
reads those same headers as its oracle, so importing them is teaching to the
test. Both documents are correct about different things, which is why this
tool exists in one narrow form only.

It builds a CEILING. The question the empty inference tier cannot currently
answer is not "can we import types" but "would types even help?" Matching
collapses at exactly the tier where types start to matter -- tiny/small 100%,
medium 10%, large 6% -- and the whole next phase of the project is staked on
that diagnosis being causal rather than correlational. A ceiling run settles it
for the price of one dev-set evaluation:

    if perfect type knowledge barely moves the number, the diagnosis is wrong
    and the inference tier is not the thing to go build.

So this writes into a SEPARATE, permanently-stamped database that the eval
machinery refuses to confuse with a real one. It is not a step toward the real
inference tier; the real one has to derive its claims from the evidence tier,
cite them, and survive retraction. This one just copies the answer key to find
out how much the answer key is worth.

    python3 -m miner.import_existing --repo ~/decomp/sbk1 \\
        --from ~/decomp/kb-sbk1.sqlite --db ~/decomp/kb-sbk1-ceiling.sqlite \\
        --ceiling
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from eval import ground_truth          # yes: the oracle. That is the taint.
from kb import provenance, tms

# Refuse to write here no matter what the flags say. The ceiling KB is a
# scratch artifact; the primary KB carries every real number the project has.
PROTECTED = {"kb-sbk1.sqlite"}

SYMBOL_LINE_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;(.*)$")
SYMBOL_TYPE_RE = re.compile(r"type:([A-Za-z_]\w*)")


def parse_symbol_addrs(path: Path) -> list[tuple[str, int, str | None]]:
    """(name, addr, type) from splat's symbol_addrs.txt. type is None if absent.

    2,082 of 2,983 entries are bare `type:func`, so this yields far less than
    the line count suggests -- names and addresses, and only ~104 typed data
    symbols. The type knowledge that matters is in the headers, not here.
    """
    out = []
    for line in path.read_text(errors="replace").splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = SYMBOL_LINE_RE.match(line)
        if not m:
            continue
        name, addr, rest = m.group(1), int(m.group(2), 16), m.group(3)
        tm = SYMBOL_TYPE_RE.search(rest)
        out.append((name, addr, tm.group(1) if tm else None))
    return out


def _assert(conn, kind, subject, value, confidence=1.0) -> int:
    """Human-origin claim: exempt from citation, per tms.assert_inference."""
    return tms.assert_inference(
        conn, kind=kind, subject=subject, value=json.dumps(value, sort_keys=True),
        evidence_ids=[], origin="human", confidence=confidence)


def import_symbols(conn, repo: Path) -> tuple[int, int]:
    path = repo / "symbol_addrs.txt"
    if not path.exists():
        return 0, 0
    names = typed = 0
    for name, addr, type_name in parse_symbol_addrs(path):
        _assert(conn, "symbol_name", f"addr:{addr:#010x}", {"name": name})
        names += 1
        # `type:func` is a classification, not a type. Only real scalar types
        # on data symbols say anything about the bytes at that address.
        if type_name and type_name != "func":
            width = ground_truth.PRIMITIVE_WIDTH.get(type_name)
            if width:
                _assert(conn, "field", f"global:{addr:#010x}",
                        {"type": type_name, "width": width})
                typed += 1
    return names, typed


def import_structs(conn, structs) -> tuple[int, int]:
    fields = sizes = 0
    for sname, struct in structs.items():
        if not struct.fields:
            continue
        for f in struct.fields:
            _assert(conn, "field", f"struct:{sname}@{f.offset:#x}", {
                "type": f.type_name, "name": f.name,
                "elem_count": f.elem_count, "is_pointer": f.is_pointer})
            fields += 1
        # Size is the last field's offset plus its width -- an approximation
        # that ignores trailing padding, so it is recorded as a lower bound
        # rather than a size claim. Invariant 5: do not assert what is unknown.
        last = max(struct.fields, key=lambda f: f.offset)
        width = (4 if last.is_pointer
                 else ground_truth.PRIMITIVE_WIDTH.get(last.type_name, 0))
        if width:
            _assert(conn, "struct_size", f"struct:{sname}",
                    {"min_size": last.offset + width * last.elem_count})
            sizes += 1
    return fields, sizes


def import_signatures(conn, sigs) -> int:
    n = 0
    for fname, params in sigs.items():
        _assert(conn, "signature", f"func:{fname}", {"params": params})
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--db", required=True, type=Path,
                    help="ceiling KB to write; created from --from if missing")
    ap.add_argument("--from", dest="src", type=Path,
                    help="clean KB to copy before importing")
    ap.add_argument("--ceiling", action="store_true",
                    help="acknowledge that this taints the KB. Required.")
    args = ap.parse_args()

    repo, db = args.repo.expanduser(), args.db.expanduser()

    if not args.ceiling:
        print("refusing: this imports the ground-truth oracle into the KB.\n"
              "It is only valid for a ceiling measurement. Pass --ceiling.",
              file=sys.stderr)
        return 2

    if db.name in PROTECTED:
        print(f"refusing: {db.name} is the primary KB. Ceiling data must never\n"
              "land there -- every number the project has quoted came from it.",
              file=sys.stderr)
        return 2

    copied = False
    if not db.exists():
        if not args.src:
            print("refusing: --db does not exist and no --from given. The "
                  "ceiling KB is a copy of a real one, not an empty file.",
                  file=sys.stderr)
            return 2
        src = args.src.expanduser()
        print(f"copying {src} -> {db}")
        shutil.copy2(src, db)
        copied = True

    conn = sqlite3.connect(str(db))

    # Importing into a KB that already holds clean attempt history would
    # retroactively make that history unquotable. A copy made moments ago is
    # the sanctioned path and is exempt -- the original is untouched, and the
    # inherited attempts are history, not results anyone will quote from here.
    if not copied and provenance.is_clean(conn):
        n = conn.execute("SELECT count(*) FROM attempts").fetchone()[0]
        if n:
            print(f"refusing: {db.name} holds {n} attempts from an untainted "
                  "run.\nImporting now would retroactively taint results that "
                  "have already\nbeen reported. Copy it to a new file first.",
                  file=sys.stderr)
            return 2

    already = conn.execute(
        "SELECT count(*) FROM inference WHERE origin='human'").fetchone()[0]
    if already:
        print(f"refusing: {already} human inference rows already present. "
              "Re-importing would double them.", file=sys.stderr)
        return 2

    structs, sigs = ground_truth.load(repo)

    names, typed = import_symbols(conn, repo)
    fields, sizes = import_structs(conn, structs)
    nsigs = import_signatures(conn, sigs)
    conn.commit()

    provenance.stamp(conn, provenance.ORACLE_TYPES,
                     f"{repo.name} headers+symbol_addrs via eval.ground_truth: "
                     f"{fields} fields, {nsigs} signatures, {names} names")

    total = conn.execute(
        "SELECT count(*) FROM inference WHERE status='active'").fetchone()[0]
    print(f"\n  symbol names   {names}")
    print(f"  typed globals  {typed}")
    print(f"  struct fields  {fields}  across {len(structs)} structs")
    print(f"  struct sizes   {sizes}")
    print(f"  signatures     {nsigs}")
    print(f"\n  inference rows now active: {total}")
    print("\n" + provenance.banner(conn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
