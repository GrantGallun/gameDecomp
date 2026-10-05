"""Generate two-way expression rewrites for the rule miner: workload-guided enumeration, a semantic check, IDO probes.

    python -m eval.rewrite_enum shapes              # term shapes that occur in our own exact sources
    python -m eval.rewrite_enum pairs               # semantically equal siblings of each shape (no compiler)
    python -m eval.rewrite_enum probe --jobs 8      # keep the pairs IDO compiles differently in small probes
    python -m eval.rewrite_enum validate --workers 8  # apply each direction to exact functions, record the residual
    python -m eval.rewrite_enum rules               # write rules.json; eval.rule_mine table merges it

The recipe is Ruler's (enumerate terms, fingerprint them on sample inputs, a class with two spellings is a candidate
rule) with Enumo's guidance (only shapes that real code contains seed the classes) and Souper-style labelling by the
real compiler (a pair IDO compiles identically in every probe context is inert and dropped). Validation is Engine A's:
the direction applied to an exact function tells which residual features that spelling produces, so a candidate
showing those features can be offered the reverse.

Seeds come only from oracle-verified exact sources (no recovered or reference-copied attempt); no held-out function
is exact, so none contributes. Evaluation semantics are C's for 32-bit int and unsigned, with IDO's wrapping; any
input on which a term is undefined (division by zero, a shift out of range) must be undefined for both spellings.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import multiprocessing
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from solver import term_rewrite as tr  # noqa: E402

OUT = ROOT / "eval" / "results" / "rewrite-enum-20260930"
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = Path("/home/grant/decomp/runs/rewrite-enum-20260930/validate.sqlite")
CONSTS = ("0", "1", "2", "8", "16", "24", "0xFF", "0xFFFF")
BINOPS = ("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>", "==", "!=", "<", "<=", ">", ">=", "&&", "||")
UNOPS = ("-", "~", "!")
MAX_OPS, MAX_VARS = 3, 3
SIBLINGS = 12
MASK = (1 << 32) - 1


# ---------------------------------------------------------------------------------------------- shapes

def _const_text(text: str):
    v = tr._int(text)
    if v is None:
        return None
    for c in CONSTS:
        if int(c, 0) == v:
            return c
    return "N"


def abstractions(node: tr.Node, source: str, budget: int = MAX_OPS):
    """(pattern text with raw leaf keys, ops) for every cut of `node` with at most `budget` operators.

    Leaves are ('E', canonical text) or ('C', const) and are numbered later, by first use."""
    node = node.strip()
    out = []
    if node.kind == "leaf" and tr._int(node.text) is not None:
        c = _const_text(node.text)
        return [(("C", c), 0)] if c != "N" else [(("N", node.text), 0)]
    if tr.side_effect_free(node):
        out.append((("E", tr._canon(source, node)), 0))
    if budget <= 0:
        return out
    if node.kind == "bin" and node.op in BINOPS:
        for (a, x), (b, y) in itertools.product(abstractions(node.kids[0], source, budget - 1),
                                                abstractions(node.kids[1], source, budget - 1)):
            if x + y + 1 <= budget:
                out.append((("bin", node.op, a, b), x + y + 1))
    elif node.kind == "un" and node.op in UNOPS:
        out += [(("un", node.op, a), x + 1) for a, x in abstractions(node.kids[0], source, budget - 1)]
    elif node.kind == "cast" and " ".join(node.text.split()) in tr.INT_CASTS:
        out += [(("cast", node.text.strip(), a), x + 1) for a, x in abstractions(node.kids[0], source, budget - 1)]
    elif node.kind == "cond":
        for parts in itertools.product(*(abstractions(k, source, budget - 1) for k in node.kids)):
            ops = sum(p[1] for p in parts) + 1
            if ops <= budget:
                out.append((("cond",) + tuple(p[0] for p in parts), ops))
    return out


def number(raw) -> str | None:
    """Pattern text with E/N placeholders numbered by first use; None past MAX_VARS or for all-constant terms."""
    names: dict = {}

    def go(t):
        tag = t[0]
        if tag in ("E", "N"):
            key = (tag, t[1])
            if key not in names:
                names[key] = f"{tag}{sum(1 for k in names if k[0] == tag)}"
            return tr.Node("leaf", text=names[key])
        if tag == "C":
            return tr.Node("leaf", text=t[1])
        if tag == "bin":
            return tr.Node("bin", t[1], [go(t[2]), go(t[3])])
        if tag == "un":
            return tr.Node("un", t[1], [go(t[2])])
        if tag == "cast":
            return tr.Node("cast", "", [go(t[2])], text=t[1])
        return tr.Node("cond", "?", [go(x) for x in t[1:]])
    n = go(raw)
    if not any(k[0] == "E" for k in names) or sum(k[0] == "E" for k in names) > MAX_VARS or \
            sum(k[0] == "N" for k in names) > 1:
        return None
    return tr.show(n)


def exact_sources():
    from eval import rule_mine
    for item in rule_mine.corpus(100000):
        path = rule_mine.CAMPAIGN if item["ledger"] == "campaign" else rule_mine.KB
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        src = db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
        db.close()
        yield item["name"], src


def run_shapes():
    from solver import rewrite_library
    counts, functions = collections.Counter(), collections.defaultdict(set)
    n = 0
    for name, src in exact_sources():
        try:
            begin, stop = rewrite_library._body(src, name)
            trees = tr.body_trees(src, begin, stop)
        except Exception:
            continue
        n += 1
        seen = set()
        for t in trees:
            for node in tr.walk(t):
                if node.kind in ("bin", "un", "cast", "cond"):
                    for raw, ops in abstractions(node, src):
                        if ops == 0:
                            continue
                        text = number(raw)
                        if text and text not in seen:
                            seen.add(text)
                            counts[text] += 1
                            functions[text].add(name)
    rows = [{"shape": s, "functions": c} for s, c in counts.most_common() if c >= 3]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "shapes.json").write_text(json.dumps({"sources": n, "shapes": rows}, indent=1))
    print(f"sources {n}, shapes {len(counts)}, in >=3 functions {len(rows)}")


# ---------------------------------------------------------------------------------------------- semantics

def samples(seed: int = 20260930) -> dict:
    """Input rows that make coincidences happen: equal operands, operands equal to the literal, edge values, random.

    Purely random rows almost never make `E0 == N0` true, so a sampler without them equates `E0 == N0` with 0."""
    r = random.Random(seed)
    edges = [0, 1, 2, 3, -1, -2, 7, 8, 15, 16, 24, 31, 32, 255, 256, 0x7FFF, 0x8000, 0xFFFF, 0x10000,
             0x7FFFFFFF, -0x80000000, 0x12345678, -0x1234]
    # values that agree with a small constant in their low byte or half only: without them `(u8)x == 2` and
    # `x == 2` fingerprint alike
    edges += [c + k for c in (0, 1, 2, 8, 16, 24, 0xFF, 0xFFFF) for k in (0x100, 0x10000, -0x100, -0x80000000)]
    rnd = lambda: r.choice(edges) if r.random() < 0.5 else r.randint(-2**31, 2**31 - 1)
    rows = []
    for v in edges:
        rows.append((v, v, v, v))
        for same in ((0, 1), (0, 2), (1, 2), (0, 3), (1, 3), (2, 3)):
            row = [rnd() for _ in range(4)]
            for k in same:
                row[k] = v
            rows.append(tuple(row))
    small = edges[:12]
    rows += [(x, y, rnd(), rnd()) for x in small for y in small]
    rows += [tuple(rnd() for _ in range(4)) for _ in range(48)]
    return {name: tuple(row[i] for row in rows) for i, name in enumerate(("E0", "E1", "E2", "N0"))}


def _w(v: int, unsigned: bool) -> int:
    v &= MASK
    return v if unsigned or v < 2**31 else v - 2**32


class Val:
    __slots__ = ("v", "u", "ub")

    def __init__(self, v, u, ub):
        self.v, self.u, self.ub = v, u, ub          # values tuple, unsigned result, undefined-input tuple


CAST_BITS = {"u8": (8, False), "s8": (8, True), "u16": (16, False), "s16": (16, True)}
_CMP = {"==": lambda x, y: x == y, "!=": lambda x, y: x != y, "<": lambda x, y: x < y,
        "<=": lambda x, y: x <= y, ">": lambda x, y: x > y, ">=": lambda x, y: x >= y}
_ARITH = {"+": lambda x, y: x + y, "-": lambda x, y: x - y, "*": lambda x, y: x * y,
          "&": lambda x, y: x & y, "|": lambda x, y: x | y, "^": lambda x, y: x ^ y}


def _div(x, y, op):
    q = abs(x) // abs(y) * (1 if (x < 0) == (y < 0) else -1)        # C truncates toward zero
    return q if op == "/" else x - q * y


def evaluate(n: tr.Node, env: dict, unsigned_vars: bool, memo: dict | None = None) -> Val:
    if memo is None:
        return _evaluate(n, env, unsigned_vars, None)
    key = tr.show(n)                    # by text: pool terms are rebuilt objects, so ids are not stable
    if key not in memo:
        memo[key] = _evaluate(n, env, unsigned_vars, memo) if tr.size(n) <= 1 else None
        if memo[key] is None:
            del memo[key]
            return _evaluate(n, env, unsigned_vars, memo)
    return memo[key]


def _evaluate(n: tr.Node, env: dict, uv: bool, memo) -> Val:
    n = n.strip()
    size = len(env["E0"])
    if n.kind == "leaf":
        if n.text in env:
            u = uv and n.text.startswith("E")
            return Val(tuple(_w(x, u) for x in env[n.text]), u, (False,) * size)
        v = int(n.text, 0)
        return Val((v,) * size, v > 0x7FFFFFFF, (False,) * size)
    kids = [evaluate(k, env, uv, memo) for k in n.kids]
    if n.kind == "cast":
        x = kids[0]
        if n.text in CAST_BITS:
            bits, signed = CAST_BITS[n.text]
            m, half = (1 << bits) - 1, 1 << (bits - 1)
            v = tuple((a & m) - ((a & m) >= half) * (m + 1) if signed else a & m for a in x.v)
            return Val(v, False, x.ub)                  # promoted to int
        u = n.text == "u32"
        return Val(tuple(_w(a, u) for a in x.v), u, x.ub)
    if n.kind == "un":
        x = kids[0]
        if n.op == "!":
            return Val(tuple(int(a == 0) for a in x.v), False, x.ub)
        if n.op == "-":
            return Val(tuple(_w(-a, x.u) for a in x.v), x.u, x.ub)
        return Val(tuple(_w(~a, x.u) for a in x.v), x.u, x.ub)
    if n.kind == "cond":
        c, a, b = kids
        u = a.u or b.u
        return Val(tuple(_w(av, u) if cv else _w(bv, u) for cv, av, bv in zip(c.v, a.v, b.v)), u,
                   tuple(cu or (au if cv else bu) for cv, cu, au, bu in zip(c.v, c.ub, a.ub, b.ub)))
    if n.kind != "bin":
        raise ValueError(n.kind)
    a, b = kids
    op = n.op
    ub0 = tuple(x or y for x, y in zip(a.ub, b.ub))
    if op == "&&":
        return Val(tuple(int(bool(x) and bool(y)) for x, y in zip(a.v, b.v)), False,
                   tuple(au or (bool(x) and bu) for x, au, bu in zip(a.v, a.ub, b.ub)))
    if op == "||":
        return Val(tuple(int(bool(x) or bool(y)) for x, y in zip(a.v, b.v)), False,
                   tuple(au or (not x and bu) for x, au, bu in zip(a.v, a.ub, b.ub)))
    if op in ("<<", ">>"):
        ub = tuple(u0 or y < 0 or y >= 32 for u0, y in zip(ub0, b.v))
        if op == "<<":
            v = tuple(_w(x << (y & 31), a.u) for x, y in zip(a.v, b.v))
        else:
            v = tuple(_w((x & MASK) >> (y & 31), True) if a.u else x >> (y & 31) for x, y in zip(a.v, b.v))
        return Val(v, a.u, ub)
    u = a.u or b.u
    xs, ys = tuple(_w(x, u) for x in a.v), tuple(_w(y, u) for y in b.v)
    if op in _CMP:
        f = _CMP[op]
        return Val(tuple(int(f(x, y)) for x, y in zip(xs, ys)), False, ub0)
    if op in ("/", "%"):
        ub = tuple(u0 or y == 0 or (not u and x == -2**31 and y == -1) for u0, x, y in zip(ub0, xs, ys))
        v = tuple(0 if bad else _w(_div(x, y, op), u) for bad, x, y in zip(ub, xs, ys))
        return Val(v, u, ub)
    f = _ARITH[op]
    return Val(tuple(_w(f(x, y), u) for x, y in zip(xs, ys)), u, ub0)


def fingerprint(n: tr.Node, env: dict, memo: tuple | None = None) -> str | None:
    """Hash of values (undefined inputs blanked) and result type, under signed and unsigned variables."""
    h = hashlib.sha256()
    for i, unsigned in enumerate((False, True)):
        try:
            r = evaluate(n, env, unsigned, memo[i] if memo else None)
        except (ValueError, KeyError):
            return None
        if all(r.ub):
            return None
        h.update(repr(tuple(None if bad else v for v, bad in zip(r.v, r.ub))).encode())
        h.update(b"u" if r.u else b"s")
    return h.hexdigest()


# ---------------------------------------------------------------------------------------------- pairs

def _leaf(t):
    return tr.Node("leaf", text=t)


def pool_terms(leaves):
    """Every term of at most two operators over `leaves` with at least one placeholder."""
    size1 = [tr.Node("bin", op, [_leaf(a), _leaf(b)]) for op in BINOPS for a in leaves for b in leaves]
    size1 += [tr.Node("un", op, [_leaf(a)]) for op in UNOPS for a in leaves]
    size1 += [tr.Node("cast", "", [_leaf(a)], text=c) for c in tr.INT_CASTS for a in leaves]
    size1 = [t for t in size1 if any(x.startswith("E") for x in tr.placeholders(t))]
    yield from size1
    for s in size1:
        for op in BINOPS:
            for a in leaves:
                yield tr.Node("bin", op, [s, _leaf(a)])
                yield tr.Node("bin", op, [_leaf(a), s])
        for op in UNOPS:
            yield tr.Node("un", op, [s])
        for c in tr.INT_CASTS:
            yield tr.Node("cast", "", [s], text=c)


def literals(term: tr.Node) -> int:
    """Number of literal leaves (N placeholders included): a sibling may not split one constant into two."""
    return sum(1 for n in tr.walk(term) if n.kind == "leaf" and not n.text.startswith("E"))


def uses(term: tr.Node) -> collections.Counter:
    """Occurrences of each placeholder: a sibling may not reuse an operand more often (`x + x == x` for `!x`)."""
    return collections.Counter(n.text for n in tr.walk(term) if n.kind == "leaf" and n.text[:1] in ("E", "N")
                               and n.text[1:].isdigit())


def padded(term: tr.Node, env: dict) -> bool:
    """True if some operation in `term` is an identity on one of its operands (`x + 0`, `(s32)(s32)x`, `!x & 1`)
    or a placeholder-free subterm is left unfolded (`16 + x + 8`). Such spellings are padding around a smaller
    rule, and IDO folds them; Ruler removes them as derivable. A spelling real code uses is kept regardless."""
    constant_fps = {fingerprint(tr.pattern(str(v)), env) for v in (0, 1)}
    for n in tr.walk(term):
        n = n.strip()
        if n.kind == "leaf":
            continue
        if not any(x.startswith("E") for x in tr.placeholders(n)):
            return True
        fp = fingerprint(n, env)
        if fp in constant_fps:
            return True                                 # `2 || x`: a constant spelled with a variable
        if any(k.strip().kind != "leaf" or k.strip().text.startswith(("E", "N")) for k in n.kids) and                 any(fingerprint(k, env) == fp for k in n.kids):
            return True
    return False


def run_pairs():
    shapes = json.loads((OUT / "shapes.json").read_text())["shapes"]
    env = samples()
    by_fp = collections.defaultdict(list)
    shape_fp = {}
    for row in shapes:
        fp = fingerprint(tr.pattern(row["shape"]), env)
        if fp:
            shape_fp[row["shape"]] = fp
            by_fp[fp].append(row["shape"])
    wanted = set(shape_fp.values())
    leaves = ["E0", "E1", "E2", "N0"] + list(CONSTS)
    n = 0
    memo = ({}, {})
    for t in pool_terms(leaves):
        n += 1
        fp = fingerprint(t, env, memo)
        if fp in wanted:
            by_fp[fp].append(tr.show(t))
        if n % 200000 == 0:
            print(f"pool {n}", flush=True)
    freq = {r["shape"]: r["functions"] for r in shapes}
    pairs = []
    for shape, fp in shape_fp.items():
        lp = tr.pattern(shape)
        own = tr.placeholders(lp)
        sibs = []
        for other in dict.fromkeys(by_fp[fp]):
            if other == shape:
                continue
            p = tr.pattern(other)
            if not tr.placeholders(p) <= own:
                continue
            if not freq.get(other) and (padded(p, env) or literals(p) > literals(lp) or
                                        any(uses(p)[v] > uses(lp)[v] for v in uses(p))):
                continue
            sibs.append((-freq.get(other, 0), tr.size(p), len(other), other))
        for _f, _s, _l, other in sorted(sibs)[:SIBLINGS]:
            pairs.append({"lhs": shape, "rhs": other, "lhs_functions": freq[shape],
                          "rhs_functions": freq.get(other, 0)})
    (OUT / "pairs.json").write_text(json.dumps({"pool": n, "shapes": len(shape_fp), "pairs": pairs}, indent=1))
    print(f"pool {n} terms, {len(shape_fp)} shapes, {len(pairs)} pairs")


# ---------------------------------------------------------------------------------------------- probes

PROBE_HEADER = """typedef signed char s8; typedef unsigned char u8; typedef short s16; typedef unsigned short u16;
typedef int s32; typedef unsigned int u32;
typedef struct { u8 a; s8 pad; s16 b; s32 c; s32 r; } Probe;
extern void probe_sink(void);
"""
CONTEXTS = {
    "ret": "s32 {name}(s32 a, s32 b, s32 c) {{ return {t}; }}",
    "cond": "void {name}(s32 a, s32 b, s32 c) {{ if ({t}) {{ probe_sink(); }} }}",
    "narrow": "s32 {name}(u8 a, s16 b, u16 c) {{ return {t}; }}",
    "field": "void {name}(Probe *p) {{ p->r = {t}; }}",
}
FIELD = {"E0": "p->a", "E1": "p->b", "E2": "p->c"}


def _probe_text(term: str, ctx: str) -> str:
    n = tr.pattern(term)
    names = FIELD if ctx == "field" else {"E0": "a", "E1": "b", "E2": "c"}
    names = {**names, "N0": "5"}
    return tr._render(n, lambda leaf: (names.get(leaf.text, leaf.text), tr.POSTFIX))[0]


def _compile_batch(args):
    batch, recipe = args
    with tempfile.TemporaryDirectory(prefix="rewrite-enum-") as tmp:
        src, obj = Path(tmp) / "probe.c", Path(tmp) / "probe.o"
        body = [PROBE_HEADER]
        for fn, term, ctx in batch:
            body.append(CONTEXTS[ctx].format(name=fn, t=_probe_text(term, ctx)))
        src.write_text("\n".join(body) + "\n")
        proc = subprocess.run([*recipe["command"], "-o", str(obj), str(src)], cwd=REPO,
                              capture_output=True, text=True, timeout=600)
        if proc.returncode or not obj.exists():
            return {"error": proc.stderr[-500:], "functions": [b[0] for b in batch]}
        dump = subprocess.run(["mips-linux-gnu-objdump", "-dr", "--no-show-raw-insn", str(obj)],
                              capture_output=True, text=True, check=True).stdout
    listings, cur = {}, None
    for line in dump.splitlines():
        h = re.match(r"^[0-9a-f]+ <([^>]+)>:$", line)
        if h:
            cur = h.group(1)
            listings[cur] = []
        elif cur and line.strip():
            m = re.match(r"^\s*[0-9a-f]+:\s+(.*)$", line)
            if m:
                listings[cur].append(re.sub(r"\s+", " ", re.sub(r"\b[0-9a-f]+ <[^>]+>", "L", m.group(1))).strip())
    return {"listings": {k: hashlib.sha256("\n".join(v).encode()).hexdigest()[:16] + f":{len(v)}"
                         for k, v in listings.items()}}


def run_probe(jobs: int):
    from tools import synthetic_corpus
    recipe = synthetic_corpus.recipe(REPO)
    pairs = json.loads((OUT / "pairs.json").read_text())["pairs"]
    terms = sorted({p["lhs"] for p in pairs} | {p["rhs"] for p in pairs})
    ids = {t: i for i, t in enumerate(terms)}
    work = [(f"pr{ids[t]}_{c}", t, c) for t in terms for c in CONTEXTS]
    batches = [work[i:i + 240] for i in range(0, len(work), 240)]
    listing, errors = {}, []
    with multiprocessing.Pool(jobs) as pool:
        for r in pool.imap_unordered(_compile_batch, [(b, recipe) for b in batches]):
            if "error" in r:
                # a batch with one bad term: fall back to compiling its functions one at a time
                errors.append(r["error"][-200:])
                for fn in r["functions"]:
                    one = next(w for w in work if w[0] == fn)
                    rr = _compile_batch(([one], recipe))
                    listing.update(rr.get("listings", {}))
            else:
                listing.update(r["listings"])
    kept, inert, broken = [], 0, 0
    for p in pairs:
        diff, ok = [], True
        for c in CONTEXTS:
            a, b = listing.get(f"pr{ids[p['lhs']]}_{c}"), listing.get(f"pr{ids[p['rhs']]}_{c}")
            if a is None or b is None:
                ok = False
                break
            if a != b:
                diff.append(c)
        if not ok:
            broken += 1
        elif diff:
            kept.append({**p, "differs_in": diff})
        else:
            inert += 1
    (OUT / "probed.json").write_text(json.dumps({"terms": len(terms), "compiled": len(listing),
                                                 "batch_errors": errors[:20], "inert": inert, "broken": broken,
                                                 "pairs": kept}, indent=1))
    print(f"terms {len(terms)}, compiled functions {len(listing)}, pairs kept {len(kept)}, inert {inert}, "
          f"broken {broken}")


# ---------------------------------------------------------------------------------------------- validation

PER_FUNCTION = 12


def _directions():
    rows = json.loads((OUT / "probed.json").read_text())["pairs"]
    out = {}
    for p in rows:
        out[f"{p['lhs']} => {p['rhs']}"] = (p["lhs"], p["rhs"])
        out[f"{p['rhs']} => {p['lhs']}"] = (p["rhs"], p["lhs"])
    return out


def validate_probe(item: dict) -> dict:
    from solver import rewrite_library, rule_miner, workspace
    from eval import rule_mine
    fn = item["name"]
    db = sqlite3.connect(f"file:{rule_mine.CAMPAIGN if item['ledger'] == 'campaign' else rule_mine.KB}?mode=ro",
                         uri=True)
    source = db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
    db.close()
    out = {"function": fn, "records": []}
    try:
        begin, stop = rewrite_library._body(source, fn)
        trees = tr.index(tr.body_trees(source, begin, stop))
        guard = tr.integer_guard(source)
    except Exception as exc:
        out["error"] = repr(exc)[:200]
        return out
    plans = []
    for key in item["directions"]:
        lhs, rhs = DIRECTIONS[key]
        news = tr.apply(source, trees, tr.pattern(lhs), tr.pattern(rhs), limit=1, guard=guard)
        if news:
            plans.append((key, news[0]))
        if len(plans) >= PER_FUNCTION:
            break
    if not plans:
        return out
    conn = sqlite3.connect(TRIAL, timeout=600)
    run_id = f"rewrite-enum-v-{int(time.time())}-{fn}"
    try:
        ws = workspace.bootstrap(REPO, fn)

        def score(code, label):
            a = workspace.score(ws, REPO, f"{fn}_rwenum_{time.time_ns()}", code, conn=conn, func=fn,
                                strategy=f"rewrite-enum:{label}"[:120], run_id=run_id, run_kind="rewrite-enum")
            conn.commit()
            return a
        base = score(source, "exact-baseline")
        if not workspace.repair_complete(base):
            out["skipped"] = "exact source does not reproduce"
            return out
        for key, new in plans:
            a = score(new, key)
            rec = {"direction": key, "compiled": bool(a.compiled), "exact": workspace.repair_complete(a)}
            if a.compiled and not rec["exact"]:
                rec["features"] = dict(rule_miner.features(a.diff or ""))
            elif not a.compiled:
                rec["stderr"] = (a.compiler_stderr or "")[-160:]
            out["records"].append(rec)
    except Exception as exc:
        out["error"] = repr(exc)[:300]
    finally:
        conn.close()
    return out


DIRECTIONS: dict = {}


def _init_worker():
    global DIRECTIONS
    DIRECTIONS = _directions()


def run_validate(workers: int, limit: int | None = None):
    from eval import rule_mine
    from solver import rewrite_library
    global DIRECTIONS
    DIRECTIONS = _directions()
    rule_mine.TRIAL = TRIAL
    rule_mine._init_trial()
    items = rule_mine.corpus(100000)
    if limit:
        items = items[:limit]
    compiled = {k: (tr.pattern(l), tr.pattern(r)) for k, (l, r) in DIRECTIONS.items()}
    # Plan globally: each function gets the directions that apply to it, least-covered first, so every direction
    # collects examples before any collects many.
    covered = collections.Counter()
    plans = []
    for item in items:
        path = rule_mine.CAMPAIGN if item["ledger"] == "campaign" else rule_mine.KB
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        src = db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
        db.close()
        try:
            begin, stop = rewrite_library._body(src, item["name"])
            trees = tr.index(tr.body_trees(src, begin, stop))
            guard = tr.integer_guard(src)
        except Exception:
            continue
        applicable = [k for k, (l, _r) in compiled.items() if next(tr.matches(trees, l, src, guard), None)]
        applicable.sort(key=lambda k: (covered[k], k))
        chosen = applicable[:PER_FUNCTION]
        covered.update(chosen)
        if chosen:
            plans.append({**item, "directions": chosen})
    print(f"directions {len(DIRECTIONS)}, functions with an application {len(plans)}, "
          f"directions with >=1 planned {len(covered)}, >=3 {sum(v >= 3 for v in covered.values())}", flush=True)
    path = OUT / "validate.jsonl"
    done = {json.loads(l)["function"] for l in path.read_text().splitlines()} if path.exists() else set()
    with multiprocessing.Pool(workers, initializer=_init_worker) as pool, path.open("a") as fh:
        for row in pool.imap_unordered(validate_probe, [p for p in plans if p["name"] not in done]):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(row["function"], len(row["records"]), row.get("skipped") or row.get("error") or "", flush=True)


# ---------------------------------------------------------------------------------------------- rules

def build_rules():
    """G:<lhs> => <rhs> entries in the rule-table format: the profile of that direction applied to exact code."""
    from solver import rule_miner
    probed = {f"{p['lhs']} => {p['rhs']}": p for p in json.loads((OUT / "probed.json").read_text())["pairs"]}
    agg = collections.defaultdict(lambda: {"applied": 0, "compiled": 0, "changed": 0, "functions": set(),
                                           "profile": collections.Counter()})
    for line in (OUT / "validate.jsonl").read_text().splitlines():
        row = json.loads(line)
        for rec in row.get("records", []):
            e = agg[rec["direction"]]
            e["applied"] += 1
            e["functions"].add(row["function"])
            e["compiled"] += rec["compiled"]
            if rec["compiled"] and not rec["exact"]:
                e["changed"] += 1
                e["profile"].update(set(rec.get("features", {})))
    rules = {}
    for key, e in agg.items():
        lhs, rhs = key.split(" => ")
        pair = probed.get(key) or probed.get(f"{rhs} => {lhs}") or {}
        effect = e["changed"] / max(1, e["compiled"])
        rules["G:" + key] = {"engine": "G", "lhs": lhs, "rhs": rhs, "applied": e["applied"],
                             "compiled": e["compiled"], "changed": e["changed"], "examples": e["changed"],
                             "functions": len(e["functions"]), "effect_rate": round(effect, 4),
                             "broke_rate": round(1 - e["compiled"] / max(1, e["applied"]), 4),
                             "differs_in": pair.get("differs_in"), "profile": dict(e["profile"].most_common(60)),
                             "pruned": effect < rule_miner.INERT or e["changed"] < 3}
    (OUT / "rules.json").write_text(json.dumps({"built": time.strftime("%Y-%m-%d %H:%M"), "rules": rules}, indent=1))
    print(f"directions validated {len(rules)}, usable {sum(not r['pruned'] for r in rules.values())}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("shapes", "pairs", "probe", "validate", "rules"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    if args.step == "shapes":
        run_shapes()
    elif args.step == "pairs":
        run_pairs()
    elif args.step == "probe":
        run_probe(args.jobs)
    elif args.step == "validate":
        run_validate(args.workers, args.limit)
    else:
        build_rules()


if __name__ == "__main__":
    main()
