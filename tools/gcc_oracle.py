"""Byte-exact oracle for Snowboard Kids 2, which is built with KMC GCC rather than IDO.

SBK2 is the second-compiler evaluation target: same developers and engine as SBK1, a different
compiler, decompiled after the local model's training cutoff. The SBK1 oracle, compiler_recipe and
the uopt register models are all IDO-specific, so this is a separate, deliberately small oracle.

Target objects need no ROM work: the local SBK2 build verifies against its SHA1, so every
`build/src/**/*.o` IS the shipped code. A candidate translation unit is compiled with the TU's own
recipe and one function is compared: EXACT means identical instruction bytes AND identical
relocations (offset, type, symbol) -- normalized text agreeing is not enough, because two
different globals print alike once addresses are erased.

Recipe flags are read from the Makefile statically (simple assignments and per-object OPT_FLAGS
overrides), with the Makefile hash recorded; no make recipe runs. The game's `textconv.py` does
run, as the real build does, because string literals compile to charmap bytes.

`selfcheck` compiles the finished decomp's own source and requires every function to come out
exact. That uses reference C to CHECK the oracle, never as input to anything a model sees.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

REPO = Path.home() / "decomp" / "sbk2"
OBJDUMP = "mips-linux-gnu-objdump"
ELF = "build/snowboardkids2.elf"
SCHEMA = Path(__file__).resolve().parents[1] / "kb" / "schema.sql"
ASSIGN = re.compile(r"(?m)^([A-Z_][A-Z0-9_]*)\s*[:?]?=\s*(.*)$")
OVERRIDE = re.compile(r"(?m)^\$\(BUILD_DIR\)/(src/\S+)\.o:\s*OPT_FLAGS\s*:=\s*(.*)$")
REF = re.compile(r"\$\(([A-Z_][A-Z0-9_]*)\)")
INSN = re.compile(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{8}\s)?)\s*([a-z][a-z0-9.]*)\s*(.*)$")
RAW_INSN = re.compile(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+([a-z][a-z0-9.]*)\s*(.*)$")
RELOC = re.compile(r"^\s*([0-9a-f]+):\s+(R_MIPS_\w+)\s+(\S+)")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- recipe -------------------------------------------------------------------

def recipe(repo: Path = REPO) -> dict:
    """Base flags and per-object optimization overrides, read without executing make."""
    text = (repo / "Makefile").read_text()
    values: dict[str, str] = {}
    for name, value in ASSIGN.findall(text):
        values.setdefault(name, value.strip())      # first plain assignment wins; `?=` defaults too

    def expand(value: str, depth: int = 0) -> str:
        if depth > 8:
            raise ValueError("recursive Makefile variable")
        return REF.sub(lambda m: expand(values.get(m.group(1), ""), depth + 1), value)

    needed = ("CFLAGS_BASE", "MACROS", "IINC")
    if any(n not in values for n in needed):
        raise ValueError("SBK2 Makefile no longer defines " + ", ".join(n for n in needed if n not in values))
    return {
        "cflags_base": expand(values["CFLAGS_BASE"]).split(),
        "macros": expand(values["MACROS"]).split(),
        "iinc": expand(values["IINC"]).split(),
        "default_opt": ["-O2"],
        "overrides": {path + ".c": value.split() for path, value in OVERRIDE.findall(text)},
        "makefile_sha256": _sha256(text.replace("\r\n", "\n").encode()),
    }


def command(rec: dict, tu: str) -> list[str]:
    """The Makefile's `$(BUILD_DIR)/src/%.o: src/%.c` compile, for TU path `src/<stem>.c`."""
    stem_dir = os.path.dirname(tu[len("src/"):])
    stem_dir = stem_dir + "/" if stem_dir else ""
    opt = rec["overrides"].get(tu, rec["default_opt"])
    return (["tools/gcc_kmc/gcc", *rec["cflags_base"], *opt, "-fno-asm", "-I", f"src/{stem_dir}",
             *rec["iinc"], *rec["macros"], "-I", stem_dir or ".", "-I", "src/", "-I", "assets/courses",
             "-I", "assets/modelpayload", "-I", "build/include", "-I", f"build/{stem_dir}",
             "-x", "c", "-c"])


def compile_unit(repo: Path, rec: dict, tu: str, source: str, obj: Path) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False, dir=obj.parent) as handle:
        handle.write(source)
        unit = Path(handle.name)
    try:
        converted = subprocess.run(["python3", "tools/textconv.py", "tools/charmap.txt", str(unit), "-"],
                                   cwd=repo, capture_output=True, text=True, timeout=120)
        if converted.returncode:
            return False, "textconv: " + converted.stderr[-800:]
        env = {**os.environ, "COMPILER_PATH": "tools/gcc_kmc"}
        proc = subprocess.run([*command(rec, tu), "-o", str(obj), "-"], cwd=repo, input=converted.stdout,
                              capture_output=True, text=True, timeout=300, env=env)
        return proc.returncode == 0 and obj.exists(), (proc.stderr or "")[-1500:]
    finally:
        unit.unlink(missing_ok=True)


# --- observe ------------------------------------------------------------------

def _function_lines(obj: Path, name: str, raw: bool) -> list[str]:
    args = [OBJDUMP, "-dr", str(obj)] if raw else [OBJDUMP, "-dr", "--no-show-raw-insn", str(obj)]
    out, inside = [], False
    for line in subprocess.run(args, capture_output=True, text=True, check=True).stdout.splitlines():
        header = re.match(r"^[0-9a-f]+ <([^>]+)>:$", line)
        if header:
            inside = header.group(1) == name
            continue
        if inside and line.strip() and line.strip() != "...":
            out.append(line.rstrip())
    return out


def fingerprint(obj: Path, name: str) -> tuple[tuple, tuple] | None:
    """(instruction words, relocations relative to the function start): the exactness key."""
    lines = _function_lines(obj, name, raw=True)
    words, relocs, start = [], [], None
    for line in lines:
        m = RAW_INSN.match(line)
        if m:
            addr = int(m.group(1), 16)
            start = addr if start is None else start
            words.append(m.group(2))
            continue
        r = RELOC.match(line)
        if r and start is not None:
            relocs.append((int(r.group(1), 16) - start, r.group(2), r.group(3)))
    return (tuple(words), tuple(relocs)) if words else None


def normalized(obj: Path, name: str) -> list[str]:
    """`op    operands` per instruction, relocation symbols folded in (the SBK1 dump format)."""
    lines = _function_lines(obj, name, raw=False)
    out: list[str] = []
    for line in lines:
        m = INSN.match(line)
        if m:
            operands = re.sub(r"\s*<[^>]*>", "", m.group(4)).strip()
            out.append(f"{m.group(3)}    {operands}".rstrip())
            continue
        r = RELOC.match(line)
        if r and out:
            kind, sym = r.group(2), r.group(3)
            op, _, operands = out[-1].partition("    ")
            if kind == "R_MIPS_HI16":
                operands = re.sub(r",[^,]*$", f",%hi({sym})", operands)
            elif kind == "R_MIPS_LO16":
                operands = (re.sub(r"^([^,]*,)?-?(?:0x)?[0-9a-f]+\(", lambda mm: f"{mm.group(1) or ''}%lo({sym})(", operands)
                            if "(" in operands else re.sub(r",[^,]*$", f",%lo({sym})", operands))
            elif kind == "R_MIPS_26":
                operands = sym
            out[-1] = f"{op}    {operands}"
    return out


def score(target_obj: Path, candidate_obj: Path | None, name: str) -> dict:
    """Compare one function. `diff` is a unified diff in the SBK1 oracle's shape."""
    target = normalized(target_obj, name)
    if candidate_obj is None:
        return {"compiled": False, "exact": False, "score": 0.0, "diff": ""}
    produced = normalized(candidate_obj, name)
    if not produced:
        return {"compiled": True, "exact": False, "score": 0.0, "diff": "", "missing_function": True}
    target_key, candidate_key = fingerprint(target_obj, name), fingerprint(candidate_obj, name)
    exact = target_key == candidate_key
    ratio = difflib.SequenceMatcher(None, target, produced, autojunk=False).ratio()
    diff = "\n".join(difflib.unified_diff(target, produced, "target_object_dump_normalized.s",
                                          "candidate_object_dump_normalized.s", lineterm=""))
    result = {"compiled": True, "exact": exact, "score": round(100 * ratio, 3), "diff": "" if exact else diff}
    if not exact and not diff:
        # Normalized text agrees and the bytes do not: swapping two `%lo(.text)` callbacks in
        # initTiltingModelTask does exactly this. Without the label a consumer sees an empty diff
        # on a failed compare and concludes there is no residual.
        result["residual"] = ("relocations-only" if target_key and candidate_key
                              and target_key[0] == candidate_key[0] else "bytes-only")
    return result


# --- inventory and knowledge base ----------------------------------------------

def inventory(repo: Path = REPO) -> list[dict]:
    """Every C-TU function with its linked address, size, instruction count and leaf status."""
    elf_addr: dict[str, list[int]] = {}
    for line in subprocess.run([OBJDUMP, "-t", str(repo / ELF)], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        parts = line.split()
        # Any section: the linked ELF places code in game segments (`.main`, `.cutscene`), not
        # `.text`. Filtering on `.text` matched nothing and every function lost its address.
        if len(parts) >= 6 and parts[2] == "F":
            elf_addr.setdefault(parts[-1], []).append(int(parts[0], 16))
    rows = []
    for obj in sorted((repo / "build" / "src").rglob("*.o")):
        tu = "src/" + obj.relative_to(repo / "build" / "src").with_suffix(".c").as_posix()
        if not (repo / tu).is_file():
            continue
        dump = subprocess.run([OBJDUMP, "-t", str(obj)], capture_output=True, text=True, check=True).stdout
        for line in dump.splitlines():
            parts = line.split()
            if len(parts) < 6 or parts[2] != "F" or parts[3] != ".text":
                continue
            name, size = parts[-1], int(parts[4], 16)
            addrs = elf_addr.get(name, [])
            body = _function_lines(obj, name, raw=False)
            rows.append({"name": name, "tu": tu, "object": obj.relative_to(repo).as_posix(), "size": size,
                         "insn_count": size // 4, "is_leaf": int(not any(re.search(r"\sjal\s", l) for l in body)),
                         "vram": addrs[0] if len(addrs) == 1 else None, "ambiguous_address": len(addrs) > 1})
    return rows


def build_kb(repo: Path, db: Path) -> dict:
    """A minimal SBK2 knowledge base on the SBK1 schema, so eval/clean_set.py works unchanged."""
    rows = inventory(repo)
    if db.exists():
        raise FileExistsError(f"refusing to overwrite {db}")
    if not any(row["vram"] is not None for row in rows):
        # The first build wrote an empty knowledge base and exited 0: every address lookup had
        # failed. An empty KB looks like a game with nothing to do.
        raise ValueError(f"no function in {len(rows)} has a unique linked address; refusing to write {db}")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text())
    tu_ids: dict[str, int] = {}
    skipped = 0
    name_count: dict[str, int] = {}
    addr_count: dict[int, int] = {}
    for row in rows:
        name_count[row["name"]] = name_count.get(row["name"], 0) + 1
        if row["vram"] is not None:
            addr_count[row["vram"]] = addr_count.get(row["vram"], 0) + 1
    for row in rows:
        # No unique identity, so never guessed: a static name defined in several TUs, a function
        # the ELF does not list once, or two symbols aliasing one address.
        if (row["vram"] is None or name_count[row["name"]] > 1 or addr_count[row["vram"]] > 1):
            skipped += 1
            continue
        if row["tu"] not in tu_ids:
            cur = conn.execute("INSERT INTO tus(name, object_path) VALUES(?, ?)", (row["tu"], row["object"]))
            tu_ids[row["tu"]] = cur.lastrowid
        conn.execute("INSERT INTO functions(addr, name, tu_id, size, insn_count, is_leaf) VALUES(?,?,?,?,?,?)",
                     (row["vram"], row["name"], tu_ids[row["tu"]], row["size"], row["insn_count"], row["is_leaf"]))
    conn.commit()
    conn.close()
    return {"functions": len(rows) - skipped, "skipped_without_unique_address": skipped, "tus": len(tu_ids)}


# --- self-check -----------------------------------------------------------------

def selfcheck(repo: Path = REPO, limit: int | None = None) -> dict:
    """Every finished function must score exact against its own target object."""
    rec = recipe(repo)
    rows = inventory(repo)
    by_tu: dict[str, list[dict]] = {}
    for row in rows:
        by_tu.setdefault(row["tu"], []).append(row)
    report = {"makefile_sha256": rec["makefile_sha256"], "tus": 0, "tus_failed_to_compile": [],
              "functions": 0, "exact": 0, "not_exact": []}
    with tempfile.TemporaryDirectory(prefix="gcc-oracle-") as tmp:
        for tu in sorted(by_tu)[:limit]:
            report["tus"] += 1
            obj = Path(tmp) / (hashlib.sha1(tu.encode()).hexdigest()[:12] + ".o")
            ok, stderr = compile_unit(repo, rec, tu, (repo / tu).read_text(encoding="utf-8", errors="replace"), obj)
            if not ok:
                report["tus_failed_to_compile"].append({"tu": tu, "stderr": stderr[-400:]})
                continue
            for row in by_tu[tu]:
                report["functions"] += 1
                result = score(repo / row["object"], obj, row["name"])
                if result["exact"]:
                    report["exact"] += 1
                else:
                    report["not_exact"].append({"tu": tu, "function": row["name"], "score": result["score"]})
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("selfcheck")
    s.add_argument("--limit", type=int)
    s.add_argument("--out", type=Path, required=True)
    k = sub.add_parser("build-kb")
    k.add_argument("--db", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "selfcheck":
        report = selfcheck(REPO, args.limit)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items()}, indent=2))
        return 0 if not report["not_exact"] and not report["tus_failed_to_compile"] else 1
    print(json.dumps(build_kb(REPO, args.db.expanduser()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
