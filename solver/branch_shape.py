"""Branch-layout repairs: C shapes whose control flow IDO 5.3 keeps and m2c flattens.

Confirmed rules (eval/results/branch-layout-20260924/PROTOCOL.md; catalog `ido53-*` entries of the same date):
  select_else     An if/else (or ternary) whose arms assign one variable keeps one path per arm to the join
                  (a `b join`); m2c writes the target's `li A; b!c join; nop; b join; li B` as `x = A; if (c) x = B;`,
                  which has one path (H1', P1'c; E1).
  o1_register_local  At -O1 a declared local gives a leaf a frame and every return then branches to one epilogue;
                  a `register` local has the frame and no store (H2, 4/4).
  empty_then_return  m2c's empty then-arm before an else, followed by `return E;`: an early return is one more path
                  to the epilogue (a `b`), which the target has and the candidate lacks.
  split_merge     m2c names a register's successive webs var_R, var_R_2, ...; a loop keeps its `slt/bnez` exit test
                  when its variable is reused by the adjacent loop (H5 P5a; the general H5 rule was refuted, so
                  this proposes the merge and the compiler decides). The webs share R in the target, so they are
                  disjoint and the merge preserves meaning.
  m2c_struct_copy  m2c's M2C_MEMCPY_ALIGNED pseudo-call as a struct assignment, which IDO lowers to its own copy loop
                  (E4); no diff gate, the pseudo-call is never real C.
  dup_return_merge  IDO emits one return tail per return statement (H3'); route m2c's marked duplicate return to the
                  one it duplicates; gated by m2c's own `Duplicate return node` marker.
The first four generators are gated on the residual signature it was built for, read from the diff (target lines `-`,
candidate lines `+`): more unconditional branches in the target, or more `slt*` in the target.
Motivating residuals (restart round 3 best nodes): drawTrainingCourseLessonEndMenu (select + merge -> 100.0),
__osAiDeviceBusy / __osSpSetPc (register local: 65.8 -> 98.3, 67.6 -> 97.7), osCartRomInit (83.6 -> 94.3),
copyGfxCommandBlockToScratch (struct copy -> 100.0), __MusIntFindChannel (dup return -> 100.0).
"""
from __future__ import annotations

import collections
import re

TYPES = {"s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64", "int", "char", "short", "long",
         "unsigned", "signed", "float", "double", "void", "struct"}


def _body(source: str, function: str):
    from solver import regalloc_mutations
    return regalloc_mutations._body(source, function)


def opcode_balance(diff: str) -> collections.Counter:
    """target count minus candidate count, per opcode, from a unified diff of target -> candidate."""
    bal = collections.Counter()
    for line in diff.splitlines():
        if line.startswith(("---", "+++")) or line[:1] not in "+-":
            continue
        op = line[1:].strip().split(" ", 1)[0].split("\t", 1)[0]
        bal[op] += 1 if line[0] == "-" else -1
    return bal


def _more_uncond(diff: str) -> bool:
    bal = opcode_balance(diff)
    return bal["b"] + bal["j"] > 0


def _more_slt(diff: str) -> bool:
    bal = opcode_balance(diff)
    return sum(v for k, v in bal.items() if k.startswith("slt")) > 0


def _calls(text: str) -> bool:
    return any(m.group(1) not in TYPES for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", text))


def _splice(source, begin, body, start, end, replacement):
    return source[:begin] + body[:start] + replacement + body[end:] + source[begin + len(body):]


def select_else(source: str, function: str, diff: str):
    """`X = A; if (C) { X = B; }` (no else) -> `if (C) { X = B; } else { X = A; }`."""
    if not _more_uncond(diff):
        return
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(
        r"^(?P<i>[ \t]*)(?P<x>[A-Za-z_]\w*) = (?P<a>[^;\n]+);[ \t]*\n"
        r"(?P=i)if \((?P<c>[^\n]+)\) \{[ \t]*\n"
        r"(?P<j>[ \t]+)(?P=x) = (?P<b>[^;\n]+);[ \t]*\n"
        r"(?P=i)\}[ \t]*\n(?![ \t]*else\b)", re.M)
    sites = []
    for m in pattern.finditer(body):
        x, a, c, b = m.group("x"), m.group("a"), m.group("c"), m.group("b")
        if re.search(rf"\b{x}\b", c + b) or _calls(a) or _calls(c):
            continue
        i, j = m.group("i"), m.group("j")
        sites.append((m, f"{i}if ({c}) {{\n{j}{x} = {b};\n{i}}} else {{\n{j}{x} = {a};\n{i}}}\n"))
    # All sites at once first: the target usually has every select as if/else, and one edit per search level
    # ran out of budget one select short (drawTrainingCourseLessonEndMenu round 4, 99.683 at depth 2).
    if len(sites) > 1:
        new, last = [], 0
        for m, text in sites:
            new += [body[last:m.start()], text]
            last = m.end()
        yield "select_else:all", source[:begin] + "".join(new) + body[last:] + source[stop:]
    for m, text in sites:
        yield f"select_else:{m.group('x')}@{m.start()}", _splice(source, begin, body, m.start(), m.end(), text)


def empty_then_return(source: str, function: str, diff: str):
    """`if (C) { } else { BODY } return E;` -> the then-arm returns E (an early return)."""
    if not _more_uncond(diff):
        return
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(r"^(?P<i>[ \t]*)if \((?P<c>[^\n]+)\) \{\s*\n(?P=i)\} else \{\n", re.M)
    for m in pattern.finditer(body):
        depth, k = 1, m.end()
        while k < len(body) and depth:
            depth += {"{": 1, "}": -1}.get(body[k], 0)
            k += 1
        tail = re.match(r"[ \t]*\n[ \t]*return (?P<e>[^;\n]+);", body[k:])
        if not tail or _calls(tail.group("e")):
            continue
        i = m.group("i")
        new = f"{i}if ({m.group('c')}) {{\n{i}    return {tail.group('e')};\n{i}}} else {{\n"
        yield f"empty_then_return@{m.start()}", _splice(source, begin, body, m.start(), m.end(), new)


def o1_register_local(source: str, function: str, diff: str, recipe: dict | None):
    """-O1 only: `if (... G ...)` reading an extern G first -> `register T v = G; if (... v ...)`."""
    opt = ((recipe or {}).get("settings") or {}).get("C_OPT")
    if opt != "-O1" or not _more_uncond(diff) or opcode_balance(diff)["addiu"] <= 0:
        return
    begin, stop = _body(source, function)
    body = source[begin:stop]
    externs = dict((name, typ) for typ, name in re.findall(r"^extern\s+(?:volatile\s+)?(\w+)\s+(\w+)\s*;", source, re.M))
    m = re.search(r"^(?P<i>[ \t]*)if \((?P<c>[^\n]+)\) \{", body, re.M)
    if not m:
        return
    # A scalar read only: `G.field`, `G[i]` or `G->f` is an aggregate, which cannot be a register local.
    used = [g.group(1) for g in re.finditer(r"\b([A-Za-z_]\w*)\b(?!\s*(?:\.|\[|->))", m.group("c"))
            if g.group(1) in externs]
    if not used or re.search(r"\bregister\b", body):
        return
    g = used[0]
    name = "reg_value"
    while re.search(rf"\b{name}\b", source):
        name += "_"
    cond = re.sub(rf"\b{g}\b", name, m.group("c"))
    new = f"{m.group('i')}register {externs[g]} {name} = {g};\n{m.group('i')}if ({cond}) {{"
    yield f"o1_register_local:{g}", _splice(source, begin, body, m.start(), m.end(), new)


_DECL = re.compile(r"^(?P<i>[ \t]*)(?P<t>(?:unsigned[ \t]+|signed[ \t]+)?[A-Za-z_]\w*(?:[ \t]*\*+)?)[ \t]+(?P<p>\**)"
                   r"(?P<n>[A-Za-z_]\w*)[ \t]*(?P<init>=[^;\n]+)?;[ \t]*$", re.M)
_NOT_TYPE = {"return", "goto", "else", "case", "register", "volatile", "static", "extern", "typedef", "struct",
             "union", "enum", "const"}
_FRAME_PAD = re.compile(r"^[ \t]*volatile[ \t]+u8[ \t]+framePad\[[^\]]+\];[ \t]*\n", re.M)


def o1_register_saved(source: str, function: str, diff: str, recipe: dict | None, limit: int = 8):
    """-O1, non-leaf included: a local the target keeps in a callee-saved register -> `register` local.

    H6 (eval/results/register-o2-20260924): at -O1 a plain local lives in its stack slot (`sw v0,N(sp)` after the
    call, `lw` before the use); a `register` local gets a saved register (`move s0,v0`) and the frame grows by its
    save. `register` on a PARAMETER changes nothing (P6b), so only locals are proposed. H7: at -O2 `register` is
    inert (36/36 references byte-identical without it), so the gate is -O1 only.
    Gate: the target saves a callee-saved register the candidate does not (`-sw s0,N(sp)` in the diff). Locals m2c
    named after a wanted register (`temp_s0`, `var_s1_2`) come first. A pipeline-invented `framePad` is offered
    removed as well, since the register save now accounts for that frame space.
    Motivating residual: __osSetGlobalIntMask (restart round 3, 75.833), `u32 temp_s0 = __osDisableInt();`.
    """
    opt = ((recipe or {}).get("settings") or {}).get("C_OPT")
    if opt != "-O1":
        return
    wanted = set(re.findall(r"^-sw\s+(s\d),", diff, re.M)) - set(re.findall(r"^\+sw\s+(s\d),", diff, re.M))
    if not wanted:
        return
    begin, stop = _body(source, function)
    body = source[begin:stop]
    decls = [m for m in _DECL.finditer(body)
             if m.group("t").split()[0] not in _NOT_TYPE and not m.group("n").startswith("framePad")]
    if not decls:
        return

    def reg_of(name):
        m = re.search(r"_(s\d)(?:_\d+)?$", name)
        return m.group(1) if m else None
    named = [m for m in decls if reg_of(m.group("n")) in wanted]
    others = [m for m in decls if m not in named]

    def mark(ms):
        new, last = [], 0
        for m in sorted(ms, key=lambda m: m.start()):
            new += [body[last:m.start("t")], "register ", m.group("t")]
            last = m.end("t")
        return "".join(new) + body[last:]

    proposals = []
    if len(named) > 1:
        proposals.append(("named", named))
    proposals += [(m.group("n"), [m]) for m in named + others]
    emitted = 0
    for label, ms in proposals:
        new = mark(ms)
        yield f"o1_register_saved:{label}", source[:begin] + new + source[stop:]
        if _FRAME_PAD.search(new):
            yield f"o1_register_saved:{label}:nopad", source[:begin] + _FRAME_PAD.sub("", new) + source[stop:]
        emitted += 1
        if emitted >= limit:
            return


def split_merge(source: str, function: str, diff: str, limit: int = 8):
    """m2c's split webs of one register: merge the whole group, then each adjacent pair."""
    if not _more_slt(diff):
        return
    begin, stop = _body(source, function)
    body = source[begin:stop]
    decls = re.findall(r"^[ \t]*(\w+)[ \t]+(var_([a-z]\d)(?:_(\d+))?)[ \t]*;[ \t]*\n", body, re.M)
    groups = collections.defaultdict(list)
    for typ, name, reg, k in decls:
        groups[(reg, typ)].append((int(k or 1), name))
    # Registers the target tests with slt (`-slti at,s1,0x40`) first: those loops are the ones the merge is for.
    wanted = set(re.findall(r"^-slt\w*\s+\w+,\s*([a-z]\d)\b", diff, re.M))
    proposals = []
    for (reg, typ), members in sorted(groups.items(), key=lambda kv: (kv[0][0] not in wanted, kv[0])):
        names = [n for _k, n in sorted(members)]
        if len(names) < 2:
            continue
        proposals.append((f"split_merge:{reg}:all", names))
        proposals += [(f"split_merge:{reg}:{a}+{b}", [a, b]) for a, b in zip(names, names[1:])] if len(names) > 2 else []
    for label, names in proposals[:limit]:
        keep, drop = names[0], names[1:]
        new = body
        for n in drop:
            new = re.sub(rf"^[ \t]*\w+[ \t]+{n}[ \t]*;[ \t]*\n", "", new, flags=re.M)
            new = re.sub(rf"\b{n}\b", keep, new)
        yield label, source[:begin] + new + source[stop:]


def m2c_struct_copy(source: str, function: str):
    """m2c's `M2C_MEMCPY_ALIGNED(D, S, N);` pseudo-call -> a whole-struct assignment of an N-byte word struct.

    IDO 5.3 compiles a struct assignment into its own copy loop (12-byte steps through `at`, `addiu end,src,N-4`,
    one-word tail); a C word loop compiles differently (E4, eval/results/branch-layout-20260924). The pseudo-call is
    never real C, so no diff gate. Motivating residual: copyGfxCommandBlockToScratch 42.0 -> 100.0.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    for m in re.finditer(r"^(?P<i>[ \t]*)M2C_MEMCPY_ALIGNED\((?P<d>[^,;()]+),\s*(?P<s>[^,;()]+),\s*"
                         r"(?P<n>0x[0-9A-Fa-f]+|\d+)\);", body, re.M):
        size = int(m.group("n"), 0)
        if size <= 0 or size % 4:
            continue
        name = f"M2cCopy{size:X}"
        new_body = body[:m.start()] + (f"{m.group('i')}*({name} *)({m.group('d').strip()}) = "
                                       f"*({name} *)({m.group('s').strip()});") + body[m.end():]
        head = source[:begin]
        if not re.search(rf"\b{name}\b", head):
            start = head.rfind("\n", 0, head.rfind(function)) + 1
            head = head[:start] + f"typedef struct {{ s32 w[{size // 4}]; }} {name};\n\n" + head[start:]
        yield f"m2c_struct_copy:{size:#x}", head + new_body + source[stop:]


def dup_return_merge(source: str, function: str):
    """Route m2c's marked duplicate return to the one return it duplicates.

    IDO emits one return tail per return statement (H3', eval/results/branch-layout-20260924), and m2c writes a
    shared target tail twice, marking the copy `Duplicate return node`. Two shapes, both meaning-preserving:
      `if (C) { /* Duplicate ... */ return E; } goto L;`       -> `if (!(C)) { goto L; }`  (falls to `return E;`)
      `if (C) { /* Duplicate ... */ return E; }` + only `}` lines + `return E;`  -> the if-block is dropped.
    In both, only closing braces may lie between the site and the surviving `return E;`.
    Motivating residual: __MusIntFindChannel 90.864 -> 100.0.
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    pattern = re.compile(
        r"^(?P<i>[ \t]*)if \((?P<c>[^\n]+)\) \{[ \t]*\n"
        r"[ \t]*/\* Duplicate return node[^\n]*\*/[ \t]*\n"
        r"(?P<j>[ \t]*)return (?P<e>[^;\n]+);[ \t]*\n"
        r"(?P=i)\}[ \t]*\n"
        r"(?:(?P=i)goto (?P<l>\w+);[ \t]*\n)?", re.M)
    for m in pattern.finditer(body):
        tail = re.match(r"(?:[ \t]*\}[ \t]*\n)*[ \t]*return (?P<e>[^;\n]+);", body[m.end():])
        if not tail or tail.group("e").strip() != m.group("e").strip():
            continue
        i, j = m.group("i"), m.group("j")
        new = f"{i}if (!({m.group('c')})) {{\n{j}goto {m.group('l')};\n{i}}}\n" if m.group("l") else ""
        yield f"dup_return_merge@{m.start()}", _splice(source, begin, body, m.start(), m.end(), new)


def at_inline(source: str, function: str):
    """m2c's `var_at` (`var_at_2` ...) back into the condition that tests it.

    `at` is the assembler temporary: IDO never homes a C variable there, so m2c's `var_at = E; ... if/while (var_at
    != 0)` is a comparison the compiler evaluated straight into a branch (possibly duplicated on several paths). Written
    as a variable it gets a real register (`slt a3` for the target's `slt at`, MusStop). Every assignment must be the
    same E and every use a truth test; the assignments and the declaration go, the tests read E. No diff gate: the
    variable is never real C. 13 unsolved functions carry one (branch-shape round 4).
    """
    begin, stop = _body(source, function)
    body = source[begin:stop]
    for decl in re.finditer(r"^[ \t]*\w+[ \t]+(var_at(?:_\d+)?)[ \t]*;[ \t]*\n", body, re.M):
        v = decl.group(1)
        assigns = list(re.finditer(rf"^[ \t]*{v}\s*=\s*(?P<e>[^;\n]+);[ \t]*\n", body, re.M))
        exprs = {re.sub(r"\s+", " ", a.group("e").strip()) for a in assigns}
        if not exprs:
            # Declared but never assigned as a statement (updateCourseSelectPlayerPanels): there is no condition to
            # inline. This raised from max() and, since variants() is one generator chain, took the function's
            # whole branch-shape pass down with it.
            continue
        if len(exprs) != 1:
            # m2c constant-propagates the comparison on one path (`var_v0 = 4; var_at = 4 < -4;`): accept when every
            # other form is the general one with a variable replaced by the constant assigned to it just before.
            general = max(exprs, key=lambda x: len(re.findall(r"\b[A-Za-z_]\w*\b", x)))
            ok = True
            for a in assigns:
                form = re.sub(r"\s+", " ", a.group("e").strip())
                if form == general:
                    continue
                prev = re.search(r"^[ \t]*([A-Za-z_]\w*)\s*=\s*([^;\n]+);[ \t]*\n\Z", body[:a.start()], re.M)
                if not prev or re.sub(rf"\b{prev.group(1)}\b", prev.group(2).strip(), general) != form:
                    ok = False
                    break
            if not ok:
                continue
            exprs = {general}
        e = exprs.pop()
        rest = body
        for a in reversed(assigns):
            rest = rest[:a.start()] + rest[a.end():]
        rest = re.sub(rf"^[ \t]*\w+[ \t]+{v}[ \t]*;[ \t]*\n", "", rest, count=1, flags=re.M)
        # A label left directly before `}` needs a statement (MusStop: `block_9:` then the removed assignment).
        rest = re.sub(r"^([ \t]*[A-Za-z_]\w*:)[ \t]*\n(?=(?:[ \t]*\n)*[ \t]*\})", r"\1;\n", rest, flags=re.M)
        rest = re.sub(rf"\b{v}\s*!=\s*0\b", f"({e})", rest)
        rest = re.sub(rf"\b{v}\s*==\s*0\b|!\s*{v}\b", f"!({e})", rest)
        rest = re.sub(rf"(\b(?:if|while)\s*\(\s*){v}(\s*\))", rf"\g<1>{e}\g<2>", rest)
        if re.search(rf"\b{v}\b", rest):
            continue
        yield f"at_inline:{v}", source[:begin] + rest + source[stop:]


def families(source: str, function: str, diff: str, recipe: dict | None = None):
    """`(kind, generator of (label, candidate))`, one per repair, for solver.regalloc_mutations.variants."""
    return [("select_else", select_else(source, function, diff)),
            ("split_merge", split_merge(source, function, diff)),
            ("o1_register_local", o1_register_local(source, function, diff, recipe)),
            ("o1_register_saved", o1_register_saved(source, function, diff, recipe)),
            ("empty_then_return", empty_then_return(source, function, diff)),
            ("m2c_struct_copy", m2c_struct_copy(source, function)),
            ("dup_return_merge", dup_return_merge(source, function)),
            ("at_inline", at_inline(source, function))]


def variants(source: str, function: str, diff: str, evidence: dict | None = None):
    """`(label, kind, candidate)` for every gated branch-shape repair."""
    for kind, gen in families(source, function, diff, (evidence or {}).get("compiler_recipe")):
        for label, candidate in gen:
            yield label, kind, candidate
