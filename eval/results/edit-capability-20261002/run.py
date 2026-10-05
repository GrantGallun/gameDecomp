"""Edit capability ladder (PROTOCOL.md): plant one known edit, ask the model to undo it at six information levels.

    python3 run.py plant [--per-class 6]        -> E/cases.jsonl   (verified: compiles, not exact)
    python3 run.py solve [--jobs 3] [--levels L0,L1,...]   -> E/attempts.jsonl (resumable)
    python3 run.py report                       -> matrix on stdout + summary.json here
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import re
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent / "draft-reference-mining-20260924"))
import mine  # noqa: E402
import pairs  # noqa: E402
from solver import llm  # noqa: E402
from tools import n64_corpus  # noqa: E402

E = Path.home() / "decomp/experiments/edit-capability-20261002"
SOURCES = [Path.home() / "decomp/experiments/context-ablation-20260924" / d for d in ("rows_binary", "rows_binary_dev")]
MODEL = "gpt-oss:20b"
LEVELS = ("L0", "L1", "L2", "L3", "L4", "S")
MAX_TARGET_LINES = 160

ID = r"[A-Za-z_]\w*"
CHAIN = rf"{ID}(?:(?:->|\.){ID}|\[\w+\])*"          # arg0->unk4, D_80[i], x.y
ATOM = rf"(?:{CHAIN}|0x[0-9A-Fa-f]+|\d+)"
KEYWORDS = {"if", "while", "for", "return", "switch", "sizeof", "else", "do", "case", "goto", "break", "continue"}
CTYPES = ("s8", "u8", "s16", "u16", "s32", "u32")
WIDEN = {"s8": "s16", "u8": "u16", "s16": "s32", "u16": "u32", "s32": "s16", "u32": "u16"}
FLIP_SIGN = {"s8": "u8", "u8": "s8", "s16": "u16", "u16": "s16", "s32": "u32", "u32": "s32"}

CLASS_TEXT = {
    "commute": "two operands of a commutative operator are written in the wrong order",
    "cmp_mirror": "a comparison is written mirrored (operands swapped and the operator reversed)",
    "if_invert": "an if statement's condition is inverted with the body moved into the else branch",
    "stmt_swap": "two adjacent statements are in the wrong order",
    "temp_return": "a returned expression goes through an extra temporary variable",
    "const": "an integer constant has the wrong value",
    "arith_op": "an arithmetic or comparison operator is wrong",
    "arg_swap": "two arguments of a call are in the wrong order",
    "drop_stmt": "a statement is missing",
    "cast_width": "a cast has the wrong width or signedness",
    "decl_width": "a local variable is declared with the wrong width or signedness",
}
KIND = {c: ("semantic" if c in {"const", "arith_op", "arg_swap", "drop_stmt", "cast_width", "decl_width"}
            else "equivalent") for c in CLASS_TEXT}


# ---------------------------------------------------------------- perturbations: def text -> [(new_def, line_index)]
def _lines(d):
    return d.split("\n")


def _body_range(lines):
    """Indices of body lines (after the opening `{` line, before the final `}`)."""
    start = next(i for i, l in enumerate(lines) if "{" in l) + 1
    end = max(i for i, l in enumerate(lines) if l.strip() == "}")
    return range(start, end)


def _is_decl(line):
    s = line.strip()
    return bool(re.match(rf"^(?:register\s+|static\s+|const\s+|volatile\s+)*(?:struct\s+)?{ID}\s*\**\s*{ID}(\[[^\]]*\])?\s*(=.*)?;$", s)) \
        and s.split()[0] not in KEYWORDS and not s.startswith("return")


def _simple_stmt(line):
    s = line.strip()
    return s.endswith(";") and "{" not in s and "}" not in s and not _is_decl(line) \
        and not re.match(r"^(return|break|continue|goto|case|default)\b", s) and s != ";"


def _sub_each(lines, rng, pattern, repl, guard=None):
    out = []
    for i in rng:
        line = lines[i]
        if _is_decl(line) or line.strip().startswith("#"):
            continue
        for m in re.finditer(pattern, line):
            if guard and not guard(line, m):
                continue
            new = repl(m)
            if new is None or new == m.group(0):
                continue
            nl = line[:m.start()] + new + line[m.end():]
            out.append((lines[:i] + [nl] + lines[i + 1:], i))
    return out


def _isolated(line, m):
    """Binary op whose operands are atoms and whose context binds no tighter than the op."""
    before = line[:m.start()].rstrip()
    after = line[m.end():].lstrip()
    ok_before = (not before) or before[-1] in "(=,!" or before.endswith("return") or before.endswith("&&") \
        or before.endswith("||")
    ok_after = (not after) or after[0] in ");,?" or after.startswith("&&") or after.startswith("||")
    head = m.group(1)
    return ok_before and ok_after and head not in KEYWORDS and not before.endswith(("*", "&"))


def p_commute(lines, rng):
    pat = rf"({ATOM}) ([+*|^]|&) ({ATOM})"
    return _sub_each(lines, rng, pat, lambda m: f"{m.group(3)} {m.group(2)} {m.group(1)}"
                     if m.group(1) != m.group(3) else None, _isolated)


MIRROR = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!="}


def p_cmp_mirror(lines, rng):
    pat = rf"({ATOM}) (<=|>=|==|!=|<|>) ({ATOM})"
    return _sub_each(lines, rng, pat, lambda m: f"{m.group(3)} {MIRROR[m.group(2)]} {m.group(1)}"
                     if m.group(1) != m.group(3) else None, _isolated)


ARITH = {"+": "-", "-": "+", "<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "==", "&": "|", "|": "&"}


def p_arith_op(lines, rng):
    pat = rf"({ATOM}) (<=|>=|==|!=|<|>|\+|-|&|\|) ({ATOM})"
    return _sub_each(lines, rng, pat, lambda m: f"{m.group(1)} {ARITH[m.group(2)]} {m.group(3)}", _isolated)


def p_const(lines, rng):
    def repl(m):
        v = int(m.group(1), 0)
        if v == 0:
            return None
        return hex(v + 1) if m.group(1).lower().startswith("0x") else str(v + 1)

    def guard(line, m):
        prev = line[:m.start()]
        return not re.search(r"[\w\[]$", prev) and "[" not in line[max(0, m.start() - 1):m.start()]
    return _sub_each(lines, rng, r"(?<![\w.])(0x[0-9A-Fa-f]+|\d+)(?![\w.])", repl, guard)


def p_cast_width(lines, rng):
    def repl(m):
        t = m.group(1)
        return f"({FLIP_SIGN[t]})" if t in ("s8", "u8", "s16", "u16") else f"({WIDEN[t]})"
    return _sub_each(lines, rng, r"\((s8|u8|s16|u16|s32|u32)\)", repl)


def p_decl_width(lines, rng):
    out = []
    for i in rng:
        m = re.match(rf"^(\s*)({'|'.join(CTYPES)})(\s+{ID}\s*(?:=.*)?;)\s*$", lines[i])
        if m:
            t = m.group(2)
            new = FLIP_SIGN[t] if t in ("s8", "u8", "s16", "u16") else WIDEN[t]
            out.append((lines[:i] + [m.group(1) + new + m.group(3)] + lines[i + 1:], i))
    return out


def p_arg_swap(lines, rng):
    pat = rf"({ID})\(({ATOM}), ({ATOM})([,)])"
    return _sub_each(lines, rng, pat, lambda m: f"{m.group(1)}({m.group(3)}, {m.group(2)}{m.group(4)}"
                     if m.group(2) != m.group(3) and m.group(1) not in KEYWORDS else None)


def p_stmt_swap(lines, rng):
    out = []
    idx = list(rng)
    for a, b in zip(idx, idx[1:]):
        la, lb = lines[a], lines[b]
        # Casts are fine; calls are not (a callee may read or write either location).
        if not (_simple_stmt(la) and _simple_stmt(lb)) or re.search(rf"{ID}\s*\(", la + lb):
            continue
        ma, mb = re.match(r"^\s*(.+?)\s*[-+*/|&^]?=(?!=)", la), re.match(r"^\s*(.+?)\s*[-+*/|&^]?=(?!=)", lb)
        if not ma or not mb:
            continue
        # Conflict = one statement's WHOLE lvalue appears in the other. Comparing identifier sets
        # rejected `arg0->unk18 = ..; arg0->unk1A = ..;` because both mention the base `arg0`:
        # found when this generator planted 0 cases from 312 adjacent simple pairs.
        wa, wb = ma.group(1).strip(), mb.group(1).strip()

        def mentions(text, lv):
            return re.search(rf"(?<![\w>.]){re.escape(lv)}(?![\w\[])", text) is not None
        if wa == wb or mentions(lb, wa) or mentions(la, wb):
            continue
        indent_a = la[:len(la) - len(la.lstrip())]
        out.append((lines[:a] + [indent_a + lb.strip(), indent_a + la.strip()] + lines[b + 1:], a))
    return out


def p_drop_stmt(lines, rng):
    return [(lines[:i] + lines[i + 1:], i) for i in rng if _simple_stmt(lines[i])]


def _ret_type(lines):
    head = " ".join(lines[:next(i for i, l in enumerate(lines) if "{" in l) + 1])
    m = re.match(rf"^\s*(?:static\s+)?((?:struct\s+)?{ID}\s*\**)\s*{ID}\s*\(", head)
    return m.group(1).strip() if m else None


def p_temp_return(lines, rng):
    rt = _ret_type(lines)
    if not rt or rt == "void":
        return []
    out = []
    first = rng.start
    for i in rng:
        m = re.match(r"^(\s*)return (.+);\s*$", lines[i])
        if m and not re.fullmatch(ID, m.group(2)):
            ind = m.group(1)
            new = lines[:first] + [f"    {rt} ec_tmp;"] + lines[first:i] + \
                [f"{ind}ec_tmp = {m.group(2)};", f"{ind}return ec_tmp;"] + lines[i + 1:]
            out.append((new, i + 1))
    return out


def p_if_invert(lines, rng):
    out = []
    for i in rng:
        m = re.match(r"^(\s*)if \((.*)\) \{\s*$", lines[i])
        if not m:
            continue
        depth, j = 0, i
        for j in range(i, len(lines)):
            depth += lines[j].count("{") - lines[j].count("}")
            if depth == 0:
                break
        if lines[j].strip() != "}":          # `} else ...` follows: only plain ifs
            continue
        ind = m.group(1)
        new = lines[:i] + [f"{ind}if (!({m.group(2)})) {{", f"{ind}}} else {{"] + lines[i + 1:]
        out.append((new, i))
    return out


PERTURB = {"commute": p_commute, "cmp_mirror": p_cmp_mirror, "if_invert": p_if_invert, "stmt_swap": p_stmt_swap,
           "temp_return": p_temp_return, "const": p_const, "arith_op": p_arith_op, "arg_swap": p_arg_swap,
           "drop_stmt": p_drop_stmt, "cast_width": p_cast_width, "decl_width": p_decl_width}


# ---------------------------------------------------------------- pool
def split(source: str, name: str):
    defs = [d for d in n64_corpus.extract_functions(source) if d["name"] == name]
    if len(defs) != 1:
        return None
    d = str(defs[0]["definition"])
    pos = source.rfind(d)
    if pos < 0:
        return None
    return source[:pos], d, source[pos + len(d):]


def pool():
    rows = {}
    for d in SOURCES:
        for p in d.glob("*.json"):
            if p.name.count(".") != 1:
                continue
            r = json.loads(p.read_text())
            if r.get("status") == "exact" and r.get("source"):
                rows[r["function"]] = r
    return sorted(rows.values(), key=lambda r: hashlib.sha256(r["function"].encode()).hexdigest())


_MIRROR = None
_LOCK = threading.Lock()


def mirror():
    global _MIRROR
    with _LOCK:
        if _MIRROR is None:
            _MIRROR = pairs.mirror_repo()
        return _MIRROR


def build(name, stem, code):
    """pairs.build, plus the certificate against the workspace's ROM-extracted target.o (`certified`)."""
    ws = pairs.workspace(mirror(), name)
    b = pairs.build(ws, stem, code)
    if b.get("compiled"):
        from solver import byte_certificate
        b["certified"] = bool(byte_certificate.certify(ws / "target.o", ws / f"{stem}.o", source=code).get("exact"))
    return b


# ---------------------------------------------------------------- plant
def plant(per_class: int, only=None, out_name: str = "cases.jsonl", exclude_from: str = ""):
    """`only` replants those classes and keeps every other class's existing cases.

    `exclude_from` names a cases file whose FUNCTIONS may not be used: a held-out set planted on
    functions the development set never touched (`--out heldout.jsonl --exclude-from cases.jsonl`).
    """
    E.mkdir(parents=True, exist_ok=True)
    kept = []
    if only and (E / out_name).exists():
        kept = [c for c in map(json.loads, open(E / out_name)) if c["class"] not in only]
    banned = {c["function"] for c in map(json.loads, open(E / exclude_from))} if exclude_from else set()
    rows = [r for r in pool() if (r.get("t_len") or 0) <= MAX_TARGET_LINES and r["function"] not in banned]
    print(f"pool: {len(rows)} clean exact functions (<= {MAX_TARGET_LINES} lines)", flush=True)
    target_cache, cases = {}, []
    tally = collections.defaultdict(collections.Counter)
    for cls, fn in PERTURB.items():
        if only and cls not in only:
            continue
        used = set()
        for r in rows:
            if len([c for c in cases if c["class"] == cls]) >= per_class:
                break
            name = r["function"]
            parts = split(r["source"], name)
            if not parts:
                tally[cls]["split-failed"] += 1
                continue
            head, d, tail = parts
            lines = _lines(d)
            try:
                rng = _body_range(lines)
            except ValueError:
                continue
            cands = fn(lines, rng)
            if not cands:
                continue
            tally[cls]["functions-with-site"] += 1
            if name not in target_cache:
                b = build(name, "ec_orig", r["source"])
                # Admission (2026-10-03): the original must CERTIFY against target.o, not only match a masked listing;
                # initControllerPakRaceRecordSaveExitMessage did not, so no repair of its planted cases could.
                target_cache[name] = mine.mask(b["dump"]) if b["compiled"] and b.get("certified") else None
            target = target_cache[name]
            if target is None:
                tally[cls]["original-not-certified"] += 1
                continue
            # deterministic pick per (class, function), try candidates until one is visible
            cands.sort(key=lambda c: hashlib.sha256(f"{cls}{name}{c[1]}{c[0]}".encode()).hexdigest())
            for new_lines, site in cands[:4]:
                new_def = "\n".join(new_lines)
                b = build(name, f"ec_p_{cls}", head + new_def + tail)
                if not b["compiled"]:
                    tally[cls]["perturbed-not-compiling"] += 1
                    continue
                dump = mine.mask(b["dump"])
                if dump == target:
                    tally[cls]["invisible"] += 1
                    continue
                tally[cls]["planted"] += 1
                cases.append({"id": f"{cls}:{name}", "class": cls, "kind": KIND[cls], "function": name,
                              "head": head, "tail": tail, "original_def": d, "perturbed_def": new_def,
                              "site_line": site, "site_text": new_lines[site].strip() if site < len(new_lines) else "",
                              "original_text": lines[site].strip() if site < len(lines) else "",
                              "target": target, "current": dump, "score": b["score"],
                              "diff": mine.gnu_diff(target, dump)})
                used.add(name)
                break
        print(cls, dict(tally[cls]), flush=True)
    with open(E / out_name, "w") as f:
        for c in kept + cases:
            f.write(json.dumps(c) + "\n")
    tally_path = HERE / ("plant_tally.json" if out_name == "cases.jsonl" else f"plant_tally_{Path(out_name).stem}.json")
    old = json.loads(tally_path.read_text()) if only and tally_path.exists() else {}
    tally_path.write_text(json.dumps(old | tally, indent=1))
    cases = kept + cases
    print(f"{len(cases)} cases -> {E / out_name}")


# ---------------------------------------------------------------- solve
def numbered(d):
    return "\n".join(f"{i + 1:3d}| {l}" for i, l in enumerate(_lines(d)))


def prompt(case, level):
    decl = case["head"].strip()
    if len(decl) > 12000:
        decl = decl[-12000:]
    p = ["You are matching a decompiled N64 function (IDO 5.3, -O2, MIPS). The C below compiles but does NOT",
         "produce the same machine code as the original. Make it compile to exactly the target.",
         "", "Declarations in scope (do not repeat them):", "```c", decl, "```", "",
         "Current function (line numbers for reference only):", "```c", numbered(case["perturbed_def"]), "```"]
    if level in ("L1", "L2", "L3", "L4"):
        p += ["", "Target assembly (normalized object dump, relocations masked):", "```", "\n".join(case["target"]), "```"]
    if level in ("L2", "L3", "L4"):
        p += ["", "Unified diff, target (-) versus what the current C compiles to (+):", "```", case["diff"].strip(), "```"]
    if level in ("L3", "L4", "S"):
        p += ["", f"The mismatch originates at line {case['site_line'] + 1}: `{case['site_text']}`"]
    if level in ("L4", "S"):
        p += [f"The problem: {CLASS_TEXT[case['class']]}."]
    p += ["", "Return the complete corrected function definition in one ```c block. Change as little as needed."]
    return "\n".join(p)


def solve_one(case, level, endpoint):
    pr = prompt(case, level)
    text, meta = llm.generate(endpoint, MODEL, pr, timeout=600, num_predict=6000, think="low", temperature=0.2,
                              seed=1, cache_dir=str(E / "llm-cache"), cache_namespace="edit-capability-v1")
    out = {"id": case["id"], "class": case["class"], "kind": case["kind"], "level": level,
           "prompt_chars": len(pr), "eval_count": meta.get("eval_count")}
    defs = [d for d in n64_corpus.extract_functions(llm.extract_c(text) or "") if d["name"] == case["function"]]
    if len(defs) != 1:
        return out | {"status": "no-definition", "response_tail": text[-400:]}
    new_def = str(defs[0]["definition"])
    b = build(case["function"], f"ec_s_{case['class']}_{level}", case["head"] + new_def + case["tail"])
    restored = case["original_text"] in [l.strip() for l in _lines(new_def)] if case["original_text"] else None
    out |= {"restored_line": restored, "def": new_def}
    if not b["compiled"]:
        return out | {"status": "not-compiled", "error": (b.get("error") or "")[-300:]}
    exact = mine.mask(b["dump"]) == case["target"]
    return out | {"status": "exact" if exact else "compiled", "score": b["score"], "base_score": case["score"],
                  "unchanged": new_def.strip() == case["perturbed_def"].strip()}


def solve(jobs: int, levels):
    cases = [json.loads(l) for l in open(E / "cases.jsonl")]
    path = E / "attempts.jsonl"
    done = set()
    if path.exists():
        done = {(r["id"], r["level"]) for r in map(json.loads, open(path))}
    todo = [(c, lv) for lv in levels for c in cases if (c["id"], lv) not in done]
    print(f"{len(todo)} attempts to run ({len(done)} done)", flush=True)
    endpoint = llm.host()
    lock = threading.Lock()

    def run(item):
        c, lv = item
        try:
            r = solve_one(c, lv, endpoint)
        except Exception as exc:  # recorded, not dropped
            r = {"id": c["id"], "class": c["class"], "kind": c["kind"], "level": lv, "status": "error",
                 "error": repr(exc)[-300:]}
        with lock:
            with open(path, "a") as f:
                f.write(json.dumps(r) + "\n")
            print(lv, c["id"], r["status"], r.get("score"), flush=True)

    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        list(ex.map(run, todo))


# ---------------------------------------------------------------- report
def report():
    rows = [json.loads(l) for l in open(E / "attempts.jsonl")]
    cases = {c["id"]: c for c in map(json.loads, open(E / "cases.jsonl"))}
    cell = collections.defaultdict(collections.Counter)
    for r in rows:
        k = (r["class"], r["level"])
        cell[k]["n"] += 1
        cell[k][r["status"]] += 1
        if r["status"] in ("compiled", "exact") and r.get("score") is not None:
            base = cases[r["id"]]["score"] or 0
            cell[k]["improved"] += r["score"] > base or r["status"] == "exact"
            cell[k]["worsened"] += r["score"] < base and r["status"] != "exact"
        if r.get("restored_line"):
            cell[k]["restored"] += 1
    classes = list(PERTURB)
    print(f"{'class':12} {'kind':10} " + " ".join(f"{lv:>8}" for lv in LEVELS) + "   (exact/n)")
    summary = {}
    for cls in classes:
        row = []
        for lv in LEVELS:
            c = cell.get((cls, lv))
            row.append(f"{c['exact']}/{c['n']}" if c else "-")
            if c:
                summary[f"{cls}|{lv}"] = dict(c)
        print(f"{cls:12} {KIND[cls]:10} " + " ".join(f"{x:>8}" for x in row))
    print()
    for kind in ("equivalent", "semantic"):
        tot = [sum(cell[(c, lv)]["exact"] for c in classes if KIND[c] == kind) for lv in LEVELS]
        n = [sum(cell[(c, lv)]["n"] for c in classes if KIND[c] == kind) for lv in LEVELS]
        print(f"{kind:23} " + " ".join(f"{a}/{b}".rjust(8) for a, b in zip(tot, n)))
    st = collections.Counter((r["level"], r["status"]) for r in rows)
    print("\nstatus by level:")
    for lv in LEVELS:
        print(lv, {s: st[(lv, s)] for s in ("exact", "compiled", "not-compiled", "no-definition", "error") if st[(lv, s)]})
    (HERE / "summary.json").write_text(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("plant", "solve", "report"))
    ap.add_argument("--per-class", type=int, default=6)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--levels", default=",".join(LEVELS))
    ap.add_argument("--classes", default="")
    ap.add_argument("--out", default="cases.jsonl")
    ap.add_argument("--exclude-from", default="")
    a = ap.parse_args()
    if a.cmd == "plant":
        plant(a.per_class, set(a.classes.split(",")) if a.classes else None, a.out, a.exclude_from)
    elif a.cmd == "solve":
        solve(a.jobs, a.levels.split(","))
    else:
        report()


if __name__ == "__main__":
    main()
