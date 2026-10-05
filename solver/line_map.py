"""Instruction <-> source-line map, built once per compile from IDO's own line records; O(1) lookups afterwards.

    lm = line_map.build(dump_text, function, listing, line_offset=k)   # objdump -dl text of the unstripped object
    lm.line_of(row)          # source line of normalized listing row `row` (None when IDO records none)
    lm.rows_of(line)         # normalized rows a source line produced
    lm.attribute(target, listing)   # lines behind every differing row, gaps for target-only rows

Keyed by POSITION (row index in the normalized listing), never by instruction text: the same text (`nop`,
`lw v0,0(a0)`) occurs many times in one function. Rows are the production normalizer's rows, so a diff's row indices
look up directly. The map is built from the CANDIDATE's compile only: a target's line table would come from the
reference source, which a real decompile does not have.

Target-only rows (instructions the candidate lacks: a missing statement) have no candidate instruction to look up;
they are attributed to the gap between the lines of their nearest aligned neighbours ("missing between lines X and Y").
Measured on the logic-v3 exam explain tasks (attribution_check.py): the plain line records name a line the reference
edit touches in 113/140 cases, median 1 line named of ~22; the misses were missing statements and declarations,
which emit no instructions of their own.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

import collections

# Interventions that could not be read (did not compile, changed nothing, probe not found): counted, never silent.
SKIPPED: collections.Counter = collections.Counter()

HEADER = re.compile(r"^([0-9a-f]+) <([^>]+)>:$")
LINE = re.compile(r"^(.+?):(\d+)(?:\s+\(discriminator \d+\))?$")
INSN = re.compile(r"^\s*([0-9a-f]+):\s+[0-9a-f]{8}\s+(.+)$")


@dataclass
class LineMap:
    lines: list            # lines[row] -> source line (int) or None
    by_line: dict = field(default_factory=dict)

    def line_of(self, row: int):
        return self.lines[row] if 0 <= row < len(self.lines) else None

    def rows_of(self, line: int) -> list:
        return self.by_line.get(line, [])

    def attribute(self, target: list[str], listing: list[str]) -> dict:
        """{"changed": {line: [candidate rows]}, "gaps": [(line_before, line_after)], "unattributed": n}."""
        changed, gaps, gap_rows, gap_spans, diff_spans, unattributed = {}, [], [], [], [], 0
        ops = difflib.SequenceMatcher(a=target, b=listing, autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in ops:
            if tag == "equal":
                continue
            diff_spans.append((i1, max(i1, i2 - 1)))      # every differing span, in TARGET rows: no class rules
            for j in range(j1, j2):
                line = self.line_of(j)
                if line is None:
                    unattributed += 1
                else:
                    changed.setdefault(line, []).append(j)
            if tag in ("delete", "replace") and i2 - i1 > j2 - j1:
                before = next((self.line_of(j) for j in range(j1 - 1, -1, -1) if self.line_of(j) is not None), None)
                after = next((self.line_of(j) for j in range(j2, len(listing)) if self.line_of(j) is not None), None)
                gaps.append((before, after))
                gap_rows.append(j1)                       # where, in the candidate listing, the missing rows belong
                gap_spans.append((i1, i2))                # where, in the TARGET listing, the extra rows sit
        return {"changed": changed, "gaps": gaps, "gap_rows": gap_rows, "gap_spans": gap_spans,
                "diff_spans": diff_spans, "unattributed": unattributed}


def records(dump: str, function: str) -> list:
    """Source line per instruction of `function`, in address order, from `objdump -dl` text."""
    inside, line, out = False, None, []
    for entry in dump.splitlines():
        m = HEADER.match(entry)
        if m:
            inside, line = m.group(2) == function, None
            continue
        if not inside:
            continue
        m = LINE.match(entry.strip())
        if m:
            line = int(m.group(2))
        elif INSN.match(entry):
            out.append(line)
    return out


def build(dump: str, function: str, listing: list[str], *, line_offset: int = 0) -> LineMap:
    """Map the normalized `listing` rows to lines. The normalizer drops only relocation rows and trailing nops, so the
    first len(listing) instructions are its rows in order; fewer records than rows means the dump does not belong to
    this listing, which is refused rather than guessed. `line_offset` re-bases file lines to function lines."""
    recs = records(dump, function)
    if len(recs) < len(listing):
        raise ValueError(f"{len(recs)} line records for {len(listing)} listing rows")
    lines = [None if r is None else r - line_offset for r in recs[:len(listing)]]
    by_line: dict = {}
    for row, line in enumerate(lines):
        if line is not None:
            by_line.setdefault(line, []).append(row)
    return LineMap(lines, by_line)


def declaration_influence(context: str, fn_text: str, function: str, compile_listing, limit: int = 12) -> dict:
    """{declaration line: set of rows of fn_text's listing that change when that declaration's type changes}.

    Declarations emit no instructions, so the line table cannot see them, but the compiler uses them: the type picks
    the load/store opcode and the extensions. This logs that influence by INTERVENTION with the compiler as-is: retype
    one scalar declaration (parameters and locals) at a time, recompile, and record which instructions move. Works for
    any compiler; `compile_listing(text) -> listing | None` is the caller's compile. `fn_text` must be the
    pycparser/CGenerator rendering (as logic-v3 functions are), so a retyped declaration changes exactly one line.
    """
    import copy
    from tools import context_closure as cc
    try:
        base_ast = cc.parse(context + "\n" + fn_text)
    except Exception:
        return {}
    funcdef = cc.find_funcdef(base_ast, function)
    if funcdef is None or cc.render_function(funcdef) != fn_text.rstrip() + "\n":
        return {}
    base = compile_listing(context + "\n" + fn_text)
    if base is None:
        return {}
    original = fn_text.rstrip().split("\n")
    alternatives = integer_types_in_scope(context)
    out: dict = {}
    for k, (decl, ident) in enumerate(list(cc._scalar_decls(funcdef))[:limit]):
        old = " ".join(ident.names)
        if old not in alternatives:
            continue
        # Retype to OTHER integer types in scope until one moves instructions: no table of which types are related.
        for new in [t for t in alternatives if t != old][:4]:
            mutated = copy.deepcopy(funcdef)
            _d, m_ident = list(cc._scalar_decls(mutated))[k]
            m_ident.names = new.split()
            text = cc.render_function(mutated)
            lines = text.rstrip().split("\n")
            changed_lines = [i + 1 for i, (x, y) in enumerate(zip(original, lines)) if x != y]
            if len(lines) != len(original) or len(changed_lines) != 1:
                break
            listing = compile_listing(context + "\n" + text)
            if listing is None:
                SKIPPED["retype-rejected-by-compiler"] += 1
                continue
            rows = _changed_rows(base, listing)
            if rows:
                out.setdefault(changed_lines[0], set()).update(rows)
                break
    return out


C_INTEGER_WORDS = {"char", "short", "int", "long", "signed", "unsigned"}    # the C language's, not a compiler's


def integer_types_in_scope(context: str) -> list[str]:
    """Integer type spellings available in the context: typedefs that resolve to C integer types, plus the builtin
    spellings it uses, in name order. Nothing here knows which types are wider or signed: the compiler shows that."""
    from pycparser import c_ast
    from tools import context_closure as cc
    try:
        ast = cc.parse(context)
    except Exception:
        return []
    names: dict[str, str] = {}
    for ext in ast.ext:
        if isinstance(ext, c_ast.Typedef) and isinstance(ext.type, c_ast.TypeDecl) \
                and isinstance(ext.type.type, c_ast.IdentifierType):
            spelled = ext.type.type.names
            resolved = " ".join(spelled)
            if set(spelled) <= C_INTEGER_WORDS or resolved in names:
                names[ext.name] = names.get(resolved, resolved)
    builtins = {" ".join(n.names) for n in _walk(ast) if isinstance(n, c_ast.IdentifierType)
                and set(n.names) <= C_INTEGER_WORDS}
    return sorted(set(names) | builtins)


def _walk(node):
    yield node
    for _name, child in node.children():
        yield from _walk(child)


def with_declarations(attribution: dict, influence: dict) -> dict:
    """Add each declaration whose influenced rows include a differing row: `{"declarations": {line: rows}}`."""
    differing = {row for rows in attribution["changed"].values() for row in rows}
    hits = {line: sorted(rows & differing) for line, rows in influence.items() if rows & differing}
    return {**attribution, "declarations": hits}


def _compounds(node, out):
    """Every compound block in a deterministic depth-first order (the same order in a deep copy)."""
    from pycparser import c_ast
    if isinstance(node, c_ast.Compound):
        out.append(node)
    for _name, child in node.children():
        _compounds(child, out)
    return out


def _parse_function(context, fn_text, function):
    from tools import context_closure as cc
    try:
        funcdef = cc.find_funcdef(cc.parse(context + "\n" + fn_text), function)
    except Exception:
        return None
    if funcdef is None or cc.render_function(funcdef) != fn_text.rstrip() + "\n":
        return None
    return funcdef


def _changed_rows(base, listing):
    rows = set()
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(a=base, b=listing, autojunk=False).get_opcodes():
        if tag != "equal":
            rows |= set(range(i1, max(i2, i1 + 1)))
    return rows


def statement_influence(context: str, fn_text: str, function: str, compile_listing) -> list:
    """[(first line, last line, rows)] per statement REGION: delete the statement (an `if` takes its whole body),
    recompile, and record which instructions of fn_text's listing move. Declarations are left to
    declaration_influence; a deletion that does not compile is skipped (counted by the caller as unknown)."""
    import copy
    from pycparser import c_ast
    from tools import context_closure as cc
    funcdef = _parse_function(context, fn_text, function)
    if funcdef is None:
        return []
    base = compile_listing(context + "\n" + fn_text)
    if base is None:
        return []
    original = fn_text.rstrip().split("\n")
    out = []
    for ci, block in enumerate(_compounds(funcdef.body, [])):
        for si, item in enumerate(block.block_items or []):
            if isinstance(item, (c_ast.Decl, c_ast.Label, c_ast.Case, c_ast.Default)):
                continue
            mutated = copy.deepcopy(funcdef)
            target_block = _compounds(mutated.body, [])[ci]
            del target_block.block_items[si]
            text = cc.render_function(mutated)
            removed = [i1 + 1 for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(
                a=original, b=text.rstrip().split("\n"), autojunk=False).get_opcodes() if tag in ("delete", "replace")
                for i1 in range(i1, i2)]
            if not removed:
                continue
            listing = compile_listing(context + "\n" + text)
            if listing is None:
                SKIPPED["deletion-does-not-compile"] += 1
                continue
            rows = _changed_rows(base, listing)
            if rows:
                out.append((min(removed), max(removed), rows))
            else:
                SKIPPED["deletion-changes-nothing"] += 1
    return out


PROBE_DECL = "extern volatile int __attr_probe;\n"


def insertion_probes(context: str, fn_text: str, function: str, compile_listing, target: list | None = None) -> list:
    """[(insert after line n, probe position in candidate rows, probe position in target rows)] for every insertion
    point in every block. The probe is a store to a volatile global: never optimized away, recognizable by its symbol.
    Positions are read by alignment, so hoisting and rescheduling around the probe do not shift them."""
    import copy
    from pycparser import c_ast
    from tools import context_closure as cc
    funcdef = _parse_function(context, fn_text, function)
    if funcdef is None:
        return []
    base = compile_listing(context + "\n" + fn_text)
    if base is None:
        return []
    # Single-statement bodies (`while (n--) *d++ = *s++;`) have no block to insert into: wrap them in braces for
    # probing. Lines are mapped back to fn_text by alignment, so the added brace lines do not shift the answer.
    blocky = copy.deepcopy(funcdef)
    _blockify(blocky.body)
    probe = cc.find_funcdef(cc.parse(PROBE_DECL + "void __p(void) { __attr_probe = 1; }"), "__p").body.block_items[0]
    original = fn_text.rstrip().split("\n")
    out = []
    for ci, block in enumerate(_compounds(blocky.body, [])):
        # EVERY position is tried; the compiler decides legality (C89 compilers reject a statement before a
        # declaration, others accept it). Rejections are counted in SKIPPED, not assumed.
        for si in range(len(block.block_items or []) + 1):
            mutated = copy.deepcopy(blocky)
            items = _compounds(mutated.body, [])[ci].block_items
            if items is None:
                items = _compounds(mutated.body, [])[ci].block_items = []
            items.insert(si, copy.deepcopy(probe))
            text = cc.render_function(mutated)
            after_line = _original_line_before(original, text.rstrip().split("\n"), "__attr_probe")
            if after_line is None:
                continue
            listing = compile_listing(PROBE_DECL + context + "\n" + text)
            if listing is None:
                SKIPPED["probe-rejected-by-compiler"] += 1
                continue
            # The probe is found by ANY row that references its symbol, in whatever form the compiler writes the
            # address. Address formation may be hoisted early, so the LAST such row (the one feeding the store) is the
            # anchor, mapped into the candidate's and the target's row coordinates by alignment.
            mentions = [r for r, ins in enumerate(listing) if "__attr_probe" in ins]
            row = mentions[-1] if mentions else None
            if row is None:
                SKIPPED["probe-store-not-found"] += 1
                continue
            out.append((after_line, _base_position(base, listing, row),
                        _base_position(target, listing, row) if target is not None else None))
    return out


def _blockify(node) -> None:
    from pycparser import c_ast
    for attr in ("iftrue", "iffalse", "stmt"):
        child = getattr(node, attr, None)
        if child is not None and not isinstance(child, c_ast.Compound):
            setattr(node, attr, c_ast.Compound([child]))
    for _name, child in node.children():
        _blockify(child)


def _original_line_before(original: list[str], probed: list[str], marker: str) -> int | None:
    """The original line the probe follows: count of original lines aligned before the probe's line."""
    p = next((i for i, line in enumerate(probed) if marker in line), None)
    if p is None:
        return None
    last = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=original, b=probed, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                if j1 + k < p:
                    last = i1 + k + 1
    return last


def _base_position(base: list[str], probed: list[str], row: int) -> int:
    """Where probed row `row` falls in `base` row coordinates (rows before it that align equal)."""
    pos = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=base, b=probed, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                if j1 + k < row:
                    pos = i1 + k + 1
    return pos


TOKEN = re.compile(r"%\w+\([^)]*\)|[A-Za-z_$][\w$.]*|-?(?:0x[0-9a-fA-F]+|\d+)|\S")


def _operands(row: str) -> list[str]:
    return TOKEN.findall(row)


def nonlocal_kind(target: list[str], listing: list[str]) -> str | None:
    """'operand-names' when every differing row pair has the same mnemonic and token shape and differs only in plain
    identifier operands (register allocation on any ISA); 'offsets' when it differs only in numbers used as a base
    displacement `N(base)` with the same base (frame layout). Derived from token shape, not a register list."""
    ops = difflib.SequenceMatcher(a=target, b=listing, autojunk=False).get_opcodes()
    if any(tag in ("insert", "delete") or (tag == "replace" and i2 - i1 != j2 - j1) for tag, i1, i2, j1, j2 in ops):
        return None
    pairs = [(a, b) for tag, i1, i2, j1, j2 in ops if tag == "replace"
             for a, b in zip(target[i1:i2], listing[j1:j2])]
    if not pairs:
        return None
    kinds = set()
    for a, b in pairs:
        ta, tb = _operands(a), _operands(b)
        if len(ta) != len(tb) or ta[0] != tb[0]:
            return None
        diffs = [(x, y, i) for i, (x, y) in enumerate(zip(ta, tb)) if x != y]
        if all(re.fullmatch(r"[A-Za-z_$][\w$]*", x) and re.fullmatch(r"[A-Za-z_$][\w$]*", y) for x, y, _i in diffs):
            kinds.add("operand-names")
        elif all(re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|\d+)", x) and i + 1 < len(ta) and ta[i + 1] == "("
                 for x, _y, i in diffs):
            kinds.add("offsets")
        else:
            return None
    return kinds.pop() if len(kinds) == 1 else None


def regions_for(differing: set, influence: list) -> list:
    """Smallest statement regions whose influence covers the differing rows (greedy cover, innermost first)."""
    chosen, left = [], set(differing)
    for first, last, rows in sorted(influence, key=lambda s: (s[1] - s[0], s[0])):
        if rows & left:
            chosen.append((first, last))
            left -= rows
        if not left:
            break
    return chosen


def insertion_points(gap_span: tuple, probes: list) -> list[int]:
    """Every insertion line whose probe lands, in TARGET row coordinates, inside or nearest the span of the target's
    extra instructions (the missing code's own position, which scheduling cannot move in the target). Ties are ALL
    returned: when several points fit the evidence equally, picking one is a guess."""
    usable = [p for p in probes if p[2] is not None]
    if not usable:
        return []
    lo, hi = gap_span

    def distance(p):
        pos = p[2]
        return 0 if lo <= pos <= hi else min(abs(pos - lo), abs(pos - hi))
    best = min(distance(p) for p in usable)
    return sorted({p[0] for p in usable if distance(p) == best})


def localize(context: str, fn_text: str, function: str, target: list[str], listing: list[str], dump: str,
             line_offset: int, compile_listing) -> dict:
    """All attribution layers for one candidate, from the candidate's own compiles only.

    direct  -- lines the line table names for differing rows AND whose deletion moves those rows (two logs agree)
    regions -- innermost statement regions whose deletion moves differing rows, plus declarations that do
    missing -- insertion points whose probe lands in the target's extra rows (all ties)
    nonlocal -- residual differs only in operand names / base offsets
    """
    lm = build(dump, function, listing, line_offset=line_offset)
    att = lm.attribute(target, listing)
    n_fn = len(fn_text.rstrip().split("\n"))
    differing = {r for rows in att["changed"].values() for r in rows}
    influence = statement_influence(context, fn_text, function, compile_listing)
    direct = sorted(n for n, rows in att["changed"].items() if 1 <= n <= n_fn and any(
        first <= n <= last and set(rows) & infl for first, last, infl in influence))
    regions = [(a, b) for a, b in regions_for(differing, influence) if 1 <= a <= n_fn]
    decls = with_declarations(att, declaration_influence(context, fn_text, function, compile_listing))["declarations"]
    missing = []
    if att["gap_spans"]:
        probes = insertion_probes(context, fn_text, function, compile_listing, target=target)
        for span in att["gap_spans"]:
            missing += [q for q in insertion_points(span, probes) if 0 <= q <= n_fn]
    return {"direct": direct, "regions": regions, "declarations": sorted(d for d in decls if 1 <= d <= n_fn),
            "missing": sorted(set(missing)), "nonlocal": nonlocal_kind(target, listing),
            "line_table": sorted(n for n in att["changed"] if 1 <= n <= n_fn)}


def render_localization(loc: dict) -> str:
    """The prompt block: compiler-derived evidence, labelled by how it was obtained, ties shown as ties."""
    out = ["WHERE THE DIFFERENCE COMES FROM (derived by compiling this function; not a guess):"]
    if loc["direct"]:
        out.append(f"  Direct: the differing instructions were emitted by line(s) {', '.join(map(str, loc['direct']))} "
                   "(line table, confirmed by deleting those statements: it moves these instructions)")
    elif loc["line_table"]:
        out.append(f"  Line table (unconfirmed): line(s) {', '.join(map(str, loc['line_table']))}")
    if loc["regions"]:
        spans = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in loc["regions"])
        out.append(f"  Region: statements whose change moves these instructions: lines {spans}")
    if loc["declarations"]:
        out.append(f"  Types: the declaration(s) on line(s) {', '.join(map(str, loc['declarations']))} decide "
                   "these instructions")
    if loc["missing"]:
        out.append("  Missing: the target has instructions with no counterpart here; they belong after line "
                   + " or ".join(map(str, loc["missing"])) + (" (tied)" if len(loc["missing"]) > 1 else ""))
    if loc["nonlocal"]:
        out.append(f"  Note: the rows differ only in {loc['nonlocal'].replace('-', ' ')} (not one source line)")
    return "\n".join(out) if len(out) > 1 else ""


def render(attribution: dict, n_lines: int) -> str:
    """Prompt/feedback text: which shown lines produced the differing rows, and where instructions are missing."""
    parts = []
    changed = {k: v for k, v in attribution["changed"].items() if 1 <= k <= n_lines}
    if changed:
        parts.append("Differing instructions come from line(s): " + ", ".join(str(k) for k in sorted(changed)))
    decls = {k for k in attribution.get("declarations", {}) if 1 <= k <= n_lines}
    if decls:
        parts.append("Their types are decided by the declaration(s) on line(s): " + ", ".join(map(str, sorted(decls))))
    for before, after in attribution["gaps"]:
        lo = before if before is not None and 1 <= before <= n_lines else None
        hi = after if after is not None and 1 <= after <= n_lines else None
        if lo or hi:
            parts.append(f"Target instructions with no counterpart are missing between line {lo or 'start'} "
                         f"and line {hi or 'end'}")
    return "\n".join(parts) or "No source line could be attributed."
