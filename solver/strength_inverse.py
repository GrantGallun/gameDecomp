"""Undo loop strength reduction: a pointer walked through an array becomes an index into it.

uopt strength-reduces `&BASE[i]` in a loop into a pointer that steps by the element size, and a decompiler
faithfully writes that pointer walk back out. The original indexing compiles differently (ugen's temporaries
and as1's scheduling follow the address arithmetic), so the walk can be a whole function's residual.
Motivating residual: waitCourseSelectRecordsClose (2026-09-23): the pointer walk sat at 97.39 through every
allocator and temporary repair; the index form, with the loop's later uses reading through the global it had
just stored, was object-exact on its own (eval/results/register-steer-20260923/attribution.py).

Shape (all must hold, else nothing is proposed):
  a pointer local P with exactly one initialisation `P = BASE;` (or `&BASE[0]`), BASE an identifier;
  exactly one step `P = (T *)((<byte type> *)P + K);`, `P += n;` or `P++;`, textually after every other use of P;
  a counter I, `I = 0;` before the initialisation, stepped `I += 1;` / `I++;` / `++I;` in the same loop, after
  every use of P (so each use sees the index of the current element).
Variants: every use of P -> `&BASE[I]` (member access `P->m` -> `BASE[I].m`); and, when the loop stores
`G = P;`, the uses after it read through G. The step's size is not checked against the element type; the
compiler decides, as for every candidate.
"""
from __future__ import annotations

import re

BYTES = r"(?:unsigned\s+char|u8|s8|char)"


def _body(source: str, function: str):
    from solver import regalloc_mutations
    return regalloc_mutations._body(source, function)


def variants(source: str, function: str):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    for decl in re.finditer(r"^[ \t]*(?:struct\s+)?\w+[ \t]*\*[ \t]*(\w+)[ \t]*;[ \t]*\n", body, re.M):
        p = decl.group(1)
        inits = list(re.finditer(rf"^[ \t]*{p}\s*=\s*(?:&\s*)?(\w+)(?:\s*\[\s*0\s*\])?\s*;[ \t]*\n", body, re.M))
        steps = list(re.finditer(
            rf"^[ \t]*(?:{p}\s*=\s*\([^()]*\*\s*\)\s*\(\s*\(\s*{BYTES}\s*\*\s*\)\s*{p}\s*\+\s*(?:0x[0-9A-Fa-f]+|\d+)\s*\)"
            rf"|{p}\s*\+=\s*\w+|{p}\s*\+\+|\+\+\s*{p})\s*;[ \t]*\n", body, re.M))
        if len(inits) != 1 or len(steps) != 1:
            continue
        base, init, step = inits[0].group(1), inits[0], steps[0]
        if base == p or init.start() > step.start():
            continue
        uses = [m for m in re.finditer(rf"\b{p}\b", body)
                if not (init.start() <= m.start() < init.end() or step.start() <= m.start() < step.end()
                        or decl.start() <= m.start() < decl.end())]
        if not uses or any(m.start() > step.start() for m in uses) or any(m.start() < init.end() for m in uses):
            continue
        counters = [m for m in re.finditer(r"^[ \t]*(?:(\w+)\s*\+=\s*1|(\w+)\s*\+\+|\+\+\s*(\w+))\s*;[ \t]*\n", body, re.M)
                    if init.end() < m.start() < step.start() + 400]
        for counter in counters:
            i = next(g for g in counter.groups() if g)
            if i == p or not re.search(rf"^[ \t]*{i}\s*=\s*0\s*;", body[:init.start()], re.M):
                continue
            if any(m.start() > counter.start() for m in uses):
                continue
            store = re.search(rf"^[ \t]*(\w+)\s*=\s*{p}\s*;[ \t]*\n", body[init.end():step.start()], re.M)
            for through_store in ([False, True] if store else [False]):
                # (start, end, replacement) over the ORIGINAL body, applied from the end so offsets stay valid
                edits = [(init.start(), init.end(), ""), (step.start(), step.end(), "")]
                for m in uses:
                    after_store = store and m.start() >= init.end() + store.end()
                    if through_store and after_store:
                        edits.append((m.start(), m.end(), store.group(1)))
                    elif body[m.end():m.end() + 2] == "->":
                        edits.append((m.start(), m.end() + 2, f"{base}[{i}]."))
                    else:
                        edits.append((m.start(), m.end(), f"&{base}[{i}]"))
                text = body
                for s, e, r in sorted(edits, reverse=True):
                    text = text[:s] + r + text[e:]
                label = f"index_form:{p}->{base}[{i}]" + (":through_" + store.group(1) if through_store else "")
                yield label, source[:begin] + text + source[stop:]


# --- Derived integer induction variables back to one counter (H6) -------------------------------------------------
# uopt strength-reduces `for (i = 0; i < N; i++) { ... i * K ... a[i] ... }` into derived variables stepped by K and by
# 1, in its own order, and moves the exit test onto one of them (`bne off, bound`). A decompiler, or a hand edit, writes
# those derived variables back out; IDO compiles the hand-derived loop differently (H6 P6a), and `i << s` is not
# `i * K` (P6b). eval/results/branch-layout-20260924 (A2). Motivating residuals: the five 99.936 siblings
# (drawCharacterSelectCoursePreviewPanel2/6/8, drawRaceSplitscreenSelectOption2/4Frame), exact with the counter form.

_NUM = r"(?:0x[0-9A-Fa-f]+|\d+)"
_WRITE = r"(?:=(?!=)|\+=|-=|\+\+|--)"


def _loops(body: str):
    """(start, end, inner_start, inner_end, cond, kind) for `do {..} while (C);` and `for (;;) {.. if (!(C)) break; }`."""
    for m in re.finditer(r"^(?P<i>[ \t]*)do \{[ \t]*\n", body, re.M):
        end = re.compile(rf"^{m.group('i')}\}} while \((?P<c>[^\n]+)\);[ \t]*\n", re.M).search(body, m.end())
        if end:
            yield m.start(), end.end(), m.end(), end.start(), end.group("c"), "do"
    for m in re.finditer(r"^(?P<i>[ \t]*)for \(;;\) \{[ \t]*\n", body, re.M):
        end = re.compile(rf"^{m.group('i')}\}}[ \t]*\n", re.M).search(body, m.end())
        if not end:
            continue
        test = re.search(r"^[ \t]*if \(!\((?P<c>[^\n]+)\)\) break;[ \t]*\n(?:[ \t]*\n)*\Z", body[m.end():end.start()], re.M)
        if test:
            yield m.start(), end.end(), m.end(), m.end() + test.start(), test.group("c"), "for"


def _self_update_only(text: str, name: str) -> bool:
    """Every mention of `name` in text is a statement `name++;`, `name--;`, `++name;` or `--name;` (dead updates)."""
    stripped = re.sub(rf"^[ \t]*(?:{name}\s*(?:\+\+|--)|(?:\+\+|--)\s*{name})\s*;[ \t]*$", "", text, flags=re.M)
    return not re.search(rf"\b{name}\b", stripped)


def counter_variants(source: str, function: str):
    begin, stop = _body(source, function)
    body = source[begin:stop]
    ints = set(re.findall(r"^[ \t]*(?:s32|int|u32)[ \t]+(\w+)[ \t]*;", body, re.M))
    for start, end, inner, inner_end, cond, kind in _loops(body):
        inside = body[inner:inner_end]
        tail = re.search(rf"(?:^[ \t]*(?:\w+\s*(?:\+=\s*{_NUM}|\+\+|=\s*{_NUM})|\+\+\s*\w+)\s*;[ \t]*\n|^[ \t]*\n)+\Z",
                         inside, re.M)
        if not tail:
            continue
        steps, junk = {}, {}
        for s in re.finditer(rf"^[ \t]*(?:(\w+)\s*\+=\s*({_NUM})|(\w+)\s*\+\+|\+\+\s*(\w+)|(\w+)\s*=\s*({_NUM}))\s*;",
                             tail.group(0), re.M):
            if s.group(5):
                junk[s.group(5)] = int(s.group(6), 0)
            else:
                v = s.group(1) or s.group(3) or s.group(4)
                if v in steps:
                    steps = {}
                    break
                steps[v] = int(s.group(2), 0) if s.group(2) else 1
        test = re.fullmatch(rf"\s*(\w+)\s*(!=|<)\s*(\w+)\s*", cond)
        if not steps or not test or test.group(1) not in steps:
            continue
        v, op, bound = test.groups()
        head = body[:start]
        starts = {}
        for u in steps:
            init = [m for m in re.finditer(rf"^[ \t]*{u}\s*=\s*({_NUM})\s*;", head, re.M)]
            if not init or re.search(rf"\b{u}\s*{_WRITE}|(?:\+\+|--)\s*{u}\b", head[init[-1].end():]) \
                    or re.search(rf"\b{u}\s*{_WRITE}|(?:\+\+|--)\s*{u}\b", inside[:tail.start()]):
                starts = {}
                break
            starts[u] = int(init[-1].group(1), 0)
        if not starts:
            continue
        # The bound: a literal, or a variable whose value at the test is one constant (junk-assigned in the tail).
        counter_pref = []
        if re.fullmatch(_NUM, bound):
            b = int(bound, 0)
        elif bound in junk and bound in ints and bound not in steps:
            b = junk[bound]
            counter_pref.append(bound)
        else:
            continue
        k = steps[v]
        span = b - starts[v]
        if k <= 0 or span <= 0 or (op == "!=" and span % k):
            continue
        n = -(-span // k)
        after = body[end:]
        if any(not _self_update_only(after, u) for u in steps):
            continue
        prev = [m.group(1) for m in re.finditer(r"^[ \t]*for \((\w+) = ", head, re.M)]
        counter_pref += prev[-1:]
        for c in counter_pref:
            if c in steps or c not in ints or not _self_update_only(after, c):
                continue
            if re.search(rf"\b{c}\b", inside[:tail.start()]):
                continue
            indent = re.match(r"[ \t]*", body[start:]).group(0)

            def expr(u):
                s0, st = starts[u], steps[u]
                term = c if st == 1 else f"{c} * {hex(st) if st >= 10 else st}"
                return term if s0 == 0 else f"({hex(s0) if s0 >= 10 else s0} + {term})"

            new_inner = inside[:tail.start()]
            for u in sorted(steps, key=len, reverse=True):
                new_inner = re.sub(rf"\b{u}\b", lambda _m, u=u: expr(u), new_inner)
            kept_junk = "".join(f"{indent}    {j} = {val:#x};\n" for j, val in junk.items() if j != c)
            loop = f"{indent}for ({c} = 0; {c} < {n}; {c}++) {{\n{new_inner}{kept_junk}{indent}}}\n"
            # An adjacent `C++; C--;` pair after the loop is a no-op that still reads C, keeping the counter live out
            # of the loop, which changes what uopt derives (the siblings: 97.45 with it, 100.0 without).
            rest = re.sub(rf"^[ \t]*{c}(\+\+|--);[ \t]*\n[ \t]*{c}(?!\1)(?:\+\+|--);[ \t]*\n", "", body[end:], flags=re.M)
            yield f"counter_loop:{v}->{c}", source[:begin] + body[:start] + loop + rest + source[stop:]
            break


def _parallel_merges(body: str):
    """`U = s; for (C = t; ...; C++, U++) { ... U ... }` -> uses of U become C (or `C + (s - t)`); the step is dropped.

    The same H6 rule for a variable stepped in lockstep with the loop's own counter: uopt derives it from the counter.
    Yields the rewritten body per loop, then all loops together first when there are several."""
    edits = []
    for m in re.finditer(rf"^(?P<i>[ \t]*)for \((?P<c>\w+) = (?P<t>{_NUM}); (?P<cond>[^;\n]+); (?P=c)\+\+, (?P<u>\w+)\+\+\) \{{[ \t]*\n",
                         body, re.M):
        c, u = m.group("c"), m.group("u")
        init = [x for x in re.finditer(rf"^[ \t]*{u}\s*=\s*({_NUM})\s*;", body[:m.start()], re.M)]
        if not init or re.search(rf"\b{u}\s*{_WRITE}|(?:\+\+|--)\s*{u}\b", body[init[-1].end():m.start()]):
            continue
        close = re.compile(rf"^{m.group('i')}\}}[ \t]*\n", re.M).search(body, m.end())
        if not close:
            continue
        inside = body[m.end():close.start()]
        if re.search(rf"\b{u}\s*{_WRITE}|(?:\+\+|--)\s*{u}\b|\b{c}\s*{_WRITE}", inside):
            continue
        delta = int(init[-1].group(1), 0) - int(m.group("t"), 0)
        repl = c if delta == 0 else f"({c} + {delta})"
        header = f"{m.group('i')}for ({c} = {m.group('t')}; {m.group('cond')}; {c}++) {{\n"
        edits.append((m.start(), close.start(), header + re.sub(rf"\b{u}\b", repl, inside), u))
    return edits


def counter_loop_variants(source: str, function: str):
    """Family `counter_loop`: parallel-counter merges and derived-variable loops (H6), combined first."""
    begin, stop = _body(source, function)
    body = source[begin:stop]
    merges = _parallel_merges(body)
    merged = body
    for s, e, text, _u in sorted(merges, reverse=True):
        merged = merged[:s] + text + merged[e:]
    both = list(counter_variants(source[:begin] + merged + source[stop:], function)) if merges else []
    seen = set()
    for label, cand in both:
        seen.add(cand)
        yield label + "+parallel", cand
    if merges:
        cand = source[:begin] + merged + source[stop:]
        if cand not in seen:
            seen.add(cand)
            yield "counter_loop:parallel:" + ",".join(u for *_x, u in merges), cand
    for label, cand in counter_variants(source, function):
        if cand not in seen:
            yield label, cand
