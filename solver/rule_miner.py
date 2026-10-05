"""Mined IDO spelling rules: features of a residual, a rule table, a matcher, and template application.

Three engines fill one table (eval/rule_mine.py builds it; patterns/mined_rules.json holds it):

  A  (Ruler / Souper)   solver.rewrite_library's generic two-way rewrites, compiled on functions we already match
                        exactly. A rule is (rewrite, direction). The compiler labels it with how often it changes
                        the listing (inert rules are pruned) and which residual features it creates.
  B  (Getafix / Revisar) abstracted parent -> child edits from logged search edges
                        (patterns.equivalences.directed_hunks), with the residual features each edit removed
                        when it improved. A template is applied to new source by placeholder matching.
  G  (Ruler / Enumo)    generated expression rewrites (eval/rewrite_enum.py): shapes from exact code, semantically
                        equal siblings by enumeration, kept only if IDO compiles them differently, then labelled on
                        exact functions like Engine A. Applied on the parse tree by solver.term_rewrite.

A feature is either a residual class (solver.residual_classes) or an abstracted instruction that one side has more
of than the other: registers, offsets and constants are blanked except the ones that carry meaning (0, 1, masks,
shift amounts), so `+andi r,r,0xff` and `-lw r,N(sp)` generalise across functions.

Matching: for an unsolved candidate's residual features F, a rule scores sum over f in F of idf(f) * p(f | rule).
idf is over rules, so a feature every rule produces (`class:registers`) says little, and one only the loop rule
produces says a lot. The compiler still decides everything; the score only orders what the search tries.
"""
from __future__ import annotations

import collections
import json
import math
import re
from pathlib import Path

TABLE = Path(__file__).resolve().parent.parent / "patterns" / "mined_rules.json"
MEANINGFUL = {"0", "1", "-1", "0x1", "0xff", "0xffff", "0x10", "0x18", "16", "24", "8", "0x8", "4", "0x4", "2", "0x2"}
_REG = re.compile(r"^(?:\$?(?:zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|fp|ra)|\$f\d+|\$\d+)$")
_BRANCHES = {"b", "beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz", "beql", "bnel", "beqzl", "bnezl",
             "bgezl", "bltzl", "bc1t", "bc1f", "j", "jal"}
INERT = 0.05               # a rule whose applications change the listing less often than this is pruned


# ---------------------------------------------------------------------------------------------- features

def abstract(instr: str) -> str:
    instr = re.sub(r"%(hi|lo|gp_rel|got|call16)\([^)]*\)", r"%\1S", instr)     # relocations: kind kept, symbol blanked
    parts = instr.replace(",", " , ").replace("(", " ( ").replace(")", " ) ").split()
    if not parts:
        return ""
    op, out = parts[0], []
    for i, tok in enumerate(parts[1:], start=1):
        nxt = parts[i + 1] if i + 1 < len(parts) else ""
        if tok in (",", "(", ")"):
            out.append(tok)
        elif tok == "sp":
            out.append("sp")
        elif _REG.match(tok):
            out.append("r")
        elif tok.startswith("%"):
            out.append(tok[:-1] + "(S)" if tok.endswith("S") else tok)
        elif re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|[0-9a-f]+|\d+)", tok) and op in _BRANCHES and i == len(parts) - 1:
            out.append("L")                   # a branch target is a label, never a constant
        elif re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|\d+)", tok):
            # an offset is never meaningful by itself (0x18 as a stack slot is not the shift amount 24)
            out.append("N" if nxt == "(" or tok not in MEANINGFUL else tok)
        else:
            out.append("L" if op in ("b", "beq", "bne", "beqz", "bnez", "bgez", "blez", "bgtz", "bltz", "j", "jal") else "Y")
    return (op + " " + "".join(out).replace(",", ",")).strip()


def features(diff: str) -> collections.Counter:
    """Residual features of one compiled candidate (target '-' vs candidate '+')."""
    from solver import diffrepair, residual_classes
    target, candidate = diffrepair._streams(diff or "")
    ct = collections.Counter(abstract(x) for x in target)
    cc = collections.Counter(abstract(x) for x in candidate)
    out = collections.Counter()
    for k, v in (ct - cc).items():
        out["-" + k] += v
    for k, v in (cc - ct).items():
        out["+" + k] += v
    try:
        for k, v in residual_classes.counts(diff or "").items():
            if v:
                out["class:" + k] += 1
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------------------------- table

def inverse(rule: str) -> str:
    """`loop:for->rotated` -> `loop:rotated->for`; symmetric rules are their own inverse."""
    family, _, direction = rule.partition(":")
    if " => " in direction:                              # generated term rewrites (eval/rewrite_enum.py)
        lhs, rhs = direction.split(" => ", 1)
        return f"{family}:{rhs} => {lhs}"
    if "->" not in direction:
        return rule
    a, b = direction.split("->", 1)
    return f"{family}:{b}->{a}"


class Table:
    """Mined rules. Engine A entries are keyed by rewrite rule, Engine B by template key."""

    def __init__(self, data: dict):
        self.data = data
        self.rules = data.get("rules", {})
        df = collections.Counter()
        for entry in self.rules.values():
            for f in entry.get("profile", {}):
                df[f] += 1
        n = max(1, len(self.rules))
        self.idf = {f: math.log((n + 1) / (c + 1)) + 0.1 for f, c in df.items()}

    @classmethod
    def load(cls, path: Path = TABLE) -> "Table | None":
        try:
            return cls(json.loads(Path(path).read_text()))
        except (OSError, ValueError):
            return None

    def usable(self, key: str) -> bool:
        e = self.rules.get(key)
        return bool(e) and not e.get("pruned")

    def score(self, key: str, residual: collections.Counter) -> float:
        e = self.rules.get(key)
        if not e or e.get("pruned"):
            return 0.0
        prof, n = e.get("profile", {}), max(1, e.get("examples", 1))
        return sum(self.idf.get(f, 0.0) * prof[f] / n for f in residual if f in prof)


# ---------------------------------------------------------------------------------------------- Engine B templates

_TOKEN = None


def _tokens_with_spans(text: str):
    global _TOKEN
    from patterns import equivalences
    if _TOKEN is None:
        _TOKEN = equivalences._TOKEN
    blanked = equivalences._COMMENT.sub(lambda m: " " * len(m.group(0)), text)
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(blanked)]


def _bind(pattern: tuple, toks: list, start: int, bindings: dict):
    """Match `pattern` (abstracted tokens) at toks[start:], extending `bindings`; returns new bindings or None."""
    b = dict(bindings)
    if start + len(pattern) > len(toks):
        return None
    for p, (t, _s, _e) in zip(pattern, toks[start:start + len(pattern)]):
        if re.fullmatch(r"[IN]\d+", p):
            if p in b:
                if b[p] != t:
                    return None
            elif (p[0] == "I" and re.fullmatch(r"[A-Za-z_]\w*", t)) or (p[0] == "N" and re.fullmatch(r"0[xX][0-9a-fA-F]+[uUlL]*|\d[\w.]*", t)):
                if t in b.values():
                    return None
                b[p] = t
            else:
                return None
        elif p != t:
            return None
    return b


def apply_template(source: str, begin: int, stop: int, hunks, limit: int = 3, toks=None):
    """New sources with the template's before-hunks replaced by its after-hunks inside source[begin:stop]."""
    if toks is None:
        toks = [t for t in _tokens_with_spans(source) if begin <= t[1] < stop]
    first_tok = hunks[0][0][0] if hunks and hunks[0][0] else None
    if first_tok and not re.fullmatch(r"[IN]\d+", first_tok) and not any(t[0] == first_tok for t in toks):
        return []                        # cheap reject: the template's first literal token never occurs
    out = []
    first, rest = hunks[0], hunks[1:]
    after_names = {p for _b, a in hunks for p in a if re.fullmatch(r"[IN]\d+", p)}
    before_names = {p for b, _a in hunks for p in b if re.fullmatch(r"[IN]\d+", p)}
    if not after_names <= before_names:
        return out                       # the after side invents a name we can't instantiate
    for i in range(len(toks)):
        b = _bind(tuple(first[0]), toks, i, {})
        if b is None:
            continue
        spans, pos, ok = [(i, i + len(first[0]))], i + len(first[0]), True
        for before, _after in rest:
            found = None
            for j in range(pos, len(toks)):
                nb = _bind(tuple(before), toks, j, b)
                if nb is not None:
                    found, b = j, nb
                    break
            if found is None:
                ok = False
                break
            spans.append((found, found + len(before)))
            pos = found + len(before)
        if not ok:
            continue
        new = source
        for (ti, tj), (_before, after) in sorted(zip(spans, hunks), key=lambda x: -x[0][0]):
            s, e = toks[ti][1], toks[tj - 1][2]
            new = new[:s] + " ".join(b.get(p, p) for p in after) + new[e:]
        if new != source:
            out.append(new)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------------------------- proposals

def _localised(scored, source: str, sites):
    """Multiply each score by 1 + the share of residual weight on the lines its edit touches.

    The matcher says WHICH rule; `sites` (site_edits.site_lines: candidate line -> mismatching instructions charged to
    it) says WHERE the residual is. Without it a rewrite was applied at its first few sites wherever they were."""
    if not sites:
        return scored
    from solver import residual_sites
    top = max(sites.values()) or 1
    out = []
    for s, key, label, new in scored:
        try:
            region = residual_sites.edit_region(source, new)
            share = sum(w for line, w in sites.items() if region["start_line"] <= line <= region["end_line"]) / top
        except Exception:
            share = 0.0
        out.append((s * (1.0 + share), key, label, new))
    return out


GENERATED_RULES = 24
GENERATED_SLOTS = 2       # of the mined lane's proposals, kept for Engine G when it has any


def _generated(source: str, begin: int, stop: int, residual, table: "Table", sites):
    """Engine G: generated two-way term rewrites. The entry for `r => l` says what applying r -> l to exact code does
    to the listing; a candidate whose residual looks like that is offered l -> r wherever it spells l."""
    from solver import term_rewrite as tr
    ranked = []
    for key, entry in table.rules.items():
        if key.startswith("G:") and not entry.get("pruned"):
            s = table.score(key, residual)
            if s > 0:
                ranked.append((s, key, entry))
    if not ranked:
        return []
    ranked.sort(key=lambda x: -x[0])
    try:
        trees = tr.index(tr.body_trees(source, begin, stop))
        guard = tr.integer_guard(source)
    except Exception:
        return []
    out, used = [], 0
    # The cap counts rules that APPLY. Capping the scored list first left G with nothing on the G3 frame: the top 24
    # scoring entries rarely match a given function, while lower-scoring ones did (37 of 50 functions).
    for s, key, entry in ranked:
        forward = inverse(key)
        lhs, rhs = forward[2:].split(" => ", 1)
        try:
            news = tr.apply(source, trees, tr.pattern(lhs), tr.pattern(rhs), limit=6 if sites else 2, guard=guard)
        except Exception:
            continue
        if news:
            out += [(s, forward, f"{lhs} -> {rhs}", new) for new in news]
            used += 1
            if used >= GENERATED_RULES:
                break
    return out


def proposals(source: str, function: str, diff: str, table: "Table | None" = None, limit: int = 8, sites=None):
    """[(score, rule_key, label, new_source)] from both engines, best first; only rules the table can score.

    `sites` (optional) localises: candidates touching residual lines rank higher, and more sites per rewrite are
    generated so that the ones on residual lines exist to be chosen."""
    table = table or Table.load()
    if table is None:
        return []
    residual = features(diff)
    if not residual:
        return []
    from solver import rewrite_library
    scored = []
    for rule, label, new in rewrite_library.all_variants(source, function, limit=12 if sites else None):
        # Engine A learned `rule` as a forward break on exact code: its profile is what the residual looks like
        # when the INVERSE spelling was needed. So a candidate variant produced by `rule` is scored on `rule`'s
        # inverse's profile.
        key = "A:" + inverse(rule)
        s = table.score(key, residual)
        if s > 0:
            scored.append((s, "A:" + rule, label, new))
    try:
        begin, stop = rewrite_library._body(source, function)
    except Exception:
        begin, stop = None, None
    if begin is not None:
        toks = [t for t in _tokens_with_spans(source) if begin <= t[1] < stop]
        for key, entry in table.rules.items():
            if not key.startswith("B:") or entry.get("pruned"):
                continue
            s = table.score(key, residual)
            if s <= 0:
                continue
            for new in apply_template(source, begin, stop, entry["hunks"], limit=2, toks=toks):
                scored.append((s, key, entry.get("pattern", key), new))
    if begin is not None:
        scored += _generated(source, begin, stop, residual, table, sites)
    scored = _localised(scored, source, sites)
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    # Engine G's slots are reserved: on the G3 frame its rewrites applied in 37 of 50 functions but ranked in the
    # top 8 in only 3, behind Engine B's 344 templates (eval/results/rewrite-enum-20260930/RESULTS.md).
    reserved = min(GENERATED_SLOTS, sum(1 for x in scored if x[1].startswith("G:")))
    for item in scored:
        if item[3] in seen or (len(out) >= limit - reserved and not item[1].startswith("G:")):
            continue
        seen.add(item[3])
        out.append(item)
        if item[1].startswith("G:"):
            reserved = max(0, reserved - 1)
        if len(out) >= limit:
            break
    out.sort(key=lambda x: -x[0])
    return out
