"""Undo m2c's loop shapes: goto loops, duplicated return nodes, rotated guards.

m2c structures a loop with an early exit as
    if (G) {
    loop_1:
        ...
        if (X) { return R; }          /* "Duplicate return node": R is also the code after the loop */
        goto loop_1;
    }
    return R;
and IDO never needed the goto: it rotates `while (C) B` into `if (C) do B while (C)` by itself. The draft census
(eval/results/draft-census-20260930) found gotos in 19.8% of unsolved drafts against 3.6% of correct sources, and
`for` missing from 21%.

`canonical()` rewrites goto loops into structured loops by rules that preserve meaning:
  - a label whose gotos are all later in the same block becomes `while (1)`; the gotos become `continue`
  - an `if` inside the loop whose body repeats the code after the loop, ending in a return, becomes `break`
  - `while (1) { if (C) break; ... }` -> `while (!C)`, `while (1) { ...; if (C) break; }` -> `do ... while (!C)`,
    and `...; if (C) continue; break;` -> `do ... while (C)`
`variants()` adds the spellings IDO may want instead: the rotated guard dropped (`if (G) do B while (C)` ->
`while (C) B`) and counted loops as `for`. The compiler decides among them.
"""
from __future__ import annotations

import copy
import re

from solver import c_stmt as cs
from solver.c_stmt import S


# ---------------------------------------------------------------------------------------------- goto loops

def _count(s: S, pred, into_loops=True) -> int:
    n = 1 if pred(s) else 0
    if not into_loops and s.kind in ("while", "do", "for", "switch"):
        return n
    return n + sum(_count(c, pred, into_loops) for c in s.body) + (_count(s.orelse, pred, into_loops) if s.orelse else 0)


def _replace(s: S, pred, make) -> S:
    if pred(s):
        return make(s)
    s = copy.copy(s)
    s.body = [_replace(c, pred, make) for c in s.body]
    if s.orelse is not None:
        s.orelse = _replace(s.orelse, pred, make)
    return s


def _free_jumps(s: S) -> bool:
    """A break or continue here that binds to a loop or switch OUTSIDE s."""
    return _count(s, lambda x: x.kind in ("break", "continue"), into_loops=False) > 0 or \
        any(_free_continue(c) for c in s.body if c.kind == "switch")


def _free_continue(s: S) -> bool:
    return _count(s.body[0] if s.body else S("block"), lambda x: x.kind == "continue", into_loops=False) > 0


def goto_loops(root: S) -> tuple[S, int]:
    """Rewrite every eligible backward goto loop; returns (new root, loops rewritten)."""
    n_done = 0
    changed = True
    while changed:
        changed = False
        for block in [x for x in cs.walk(root) if x.kind == "block"]:
            L = block.body
            for i, item in enumerate(L):
                if item.kind != "label":
                    continue
                name = item.text
                is_goto = lambda x, name=name: x.kind == "goto" and x.text == name
                total = _count(root, is_goto)
                if not total:
                    continue
                later = [j for j in range(i + 1, len(L)) if _count(L[j], is_goto)]
                if not later:
                    continue
                j = later[-1]
                region = L[i + 1:j + 1]
                region_block = S("block", body=region)
                if _count(region_block, is_goto) != total or _count(region_block, is_goto, into_loops=False) != total:
                    continue                  # a goto from elsewhere, or one inside a nested loop
                if _count(region_block, lambda x: x.kind in ("break", "continue"), into_loops=False):
                    continue                  # would rebind to the new loop
                if any(_count(r, lambda x: x.kind == "label") for r in region):
                    continue                  # another label inside: leave it to a later pass
                body = [_replace(r, is_goto, lambda _x: S("continue")) for r in region]
                if body and body[-1].kind == "continue":
                    body = body[:-1]
                else:
                    body.append(S("break"))
                block.body = L[:i] + [S("while", "1", [S("block", body=body)])] + L[j + 1:]
                n_done += 1
                changed = True
                break
            if changed:
                break
    return root, n_done


# ---------------------------------------------------------------------------------------------- canonical loops

def _tail_returns(items: list) -> bool:
    return bool(items) and items[-1].kind == "return"


def _dedupe_returns(loop: S, post: list) -> int:
    """`if (X) { <post> }` inside the loop, where post (the code after the loop) ends in a return -> `if (X) break;`."""
    if not _tail_returns(post):
        return 0
    n = 0

    def visit(items):
        nonlocal n
        for k, s in enumerate(items):
            if s.kind == "if":
                then = cs.items(s.body[0])
                if s.orelse is None and cs.same(then, post):
                    items[k] = S("if", s.text, [S("block", body=[S("break")])])
                    n += 1
                    continue
                visit(then)
                if s.orelse is not None:
                    visit(cs.items(s.orelse))
            elif s.kind == "block":
                visit(s.body)
    visit(cs.items(loop.body[0]))
    return n


def _normalise_ifs(items: list):
    for k, s in enumerate(items):
        if s.kind == "if" and not cs.items(s.body[0]) and s.orelse is not None:
            items[k] = S("if", cs.negate(s.text), [s.orelse if s.orelse.kind == "block" else S("block", body=[s.orelse])])


def _only(s: S, kind: str) -> bool:
    body = cs.items(s)
    return len(body) == 1 and body[0].kind == kind


def _canon_loop(loop: S) -> S:
    body = list(cs.items(loop.body[0]))
    _normalise_ifs(body)
    if body and body[-1].kind == "continue":
        body = body[:-1]
    first, last = (body[0] if body else None), (body[-1] if body else None)
    if first and first.kind == "if" and first.orelse is None and _only(first.body[0], "break"):
        return S("while", cs.negate(first.text), [S("block", body=body[1:])])
    if last and last.kind == "if" and last.orelse is None and _only(last.body[0], "break"):
        return S("do", cs.negate(last.text), [S("block", body=body[:-1])])
    if len(body) >= 2 and last.kind == "break" and body[-2].kind == "if" and body[-2].orelse is None \
            and _only(body[-2].body[0], "continue"):
        return S("do", body[-2].text, [S("block", body=body[:-2])])
    if len(body) >= 2 and last.kind == "break" and body[-2].kind == "if" and body[-2].orelse is None:
        then = cs.items(body[-2].body[0])
        if then and then[-1].kind == "continue" and len(then) > 1:
            rest = body[:-2] + [S("if", cs.negate(body[-2].text), [S("block", body=[S("break")])])] + then[:-1]
            return _canon_loop(S("while", "1", [S("block", body=rest)]))
    if last and last.kind == "if" and last.orelse is not None:
        a, b = cs.items(last.body[0]), cs.items(last.orelse)
        if len(a) == 1 and len(b) == 1 and {a[0].kind, b[0].kind} == {"break", "continue"}:
            cond = last.text if a[0].kind == "continue" else cs.negate(last.text)
            return S("do", cond, [S("block", body=body[:-1])])
    return S("while", "1", [S("block", body=body)])


def _post(seq: list | None) -> list:
    """The straight-line code a loop exit runs, up to and including its return; [] when it is not straight-line."""
    if not seq:
        return []
    out = []
    for s in seq:
        if s.kind == "return":
            return out + [s]
        if s.kind not in ("expr",):
            return []
        out.append(s)
    return []


def canonical(root: S) -> tuple[S, dict]:
    """Goto loops structured, duplicated return nodes folded into break, while(1) turned into while/do-while."""
    root = copy.deepcopy(root)
    root, n_goto = goto_loops(root)
    stats = {"goto_loops": n_goto, "deduped_returns": 0, "canonical_loops": 0}

    def process(items: list, cont: list | None) -> bool:
        changed = False
        for k in range(len(items)):
            s = items[k]
            after = items[k + 1:] + (cont or []) if cont is not None else items[k + 1:]
            if s.kind == "while" and s.text == "1":
                stats["deduped_returns"] += _dedupe_returns(s, _post(after))
                new = _canon_loop(s)
                if cs.render([new]) != cs.render([s]):
                    items[k] = s = new
                    stats["canonical_loops"] += new.text != "1"
                    changed = True
            if s.kind == "if":
                changed |= process(cs.items(s.body[0]) if s.body[0].kind == "block" else s.body, after)
                if s.orelse is not None:
                    changed |= process(cs.items(s.orelse) if s.orelse.kind == "block" else [s.orelse], after)
            elif s.kind == "block":
                changed |= process(s.body, after)
            elif s.kind in ("while", "do", "for", "switch"):
                inner = s.body[0]
                changed |= process(inner.body if inner.kind == "block" else s.body, None)
        return changed

    for _ in range(6):
        if not process(root.body, []):
            break
    return root, stats


# ---------------------------------------------------------------------------------------------- alternatives

ASSIGN = re.compile(r"^(?P<var>[A-Za-z_]\w*)\s*=\s*(?P<val>[^;]+);$")
STEP = re.compile(r"^(?:(?P<a>[A-Za-z_]\w*)\s*(?:\+=|-=)\s*[^;]+|(?P<b>[A-Za-z_]\w*)\s*(?:\+\+|--)|"
                  r"(?:\+\+|--)\s*(?P<c>[A-Za-z_]\w*)|(?P<d>[A-Za-z_]\w*)\s*=\s*(?P=d)\s*[-+]\s*[^;]+);$")


def _unrotate_sites(root: S):
    """(block, index) of `if (G) { do B while (C); }` with nothing else in the if."""
    for block in [x for x in cs.walk(root) if x.kind == "block"]:
        for k, s in enumerate(block.body):
            if s.kind == "if" and s.orelse is None:
                inner = cs.items(s.body[0])
                if len(inner) == 1 and inner[0].kind == "do":
                    yield block, k


def _step_var(text: str) -> str | None:
    m = STEP.match(text)
    return next(g for g in m.groups() if g) if m else None


def _for_plans(root: S):
    """(block, loop index, init index, step indexes) for while loops whose test variable is initialised just before
    (among the preceding simple assignments) and stepped in the body's trailing run of step statements."""
    for block in [x for x in cs.walk(root) if x.kind == "block"]:
        for k, s in enumerate(block.body):
            if s.kind != "while" or s.text == "1":
                continue
            body = cs.items(s.body[0])
            tail = []
            for j in range(len(body) - 1, -1, -1):
                if body[j].kind == "expr" and _step_var(body[j].text):
                    tail.insert(0, j)
                else:
                    break
            tested = [j for j in tail if re.search(rf"\b{re.escape(_step_var(body[j].text))}\b", s.text)]
            if not tested:
                continue
            var = _step_var(body[tested[0]].text)
            init = None
            for j in range(k - 1, -1, -1):
                m = ASSIGN.match(block.body[j].text) if block.body[j].kind == "expr" else None
                if not m:
                    break
                if m.group("var") == var:
                    init = j
                    break
            if init is None:
                continue
            yield block, k, init, tested[0], tail


def _to_for(block, k, init, step, tail, all_steps=False):
    s = block.body[k]
    body = cs.items(s.body[0])
    steps = tail if all_steps else [step]
    step_text = ", ".join(body[j].text.rstrip(";") for j in steps)
    new_body = [x for j, x in enumerate(body) if j not in steps]
    loop = S("for", s.text, [S("block", body=new_body)], init=block.body[init].text.rstrip(";"), step=step_text)
    block.body[k] = loop
    del block.body[init]


def _unrotate(block, k):
    s = block.body[k]
    do = cs.items(s.body[0])[0]
    block.body[k] = S("while", do.text, do.body)


def _assign_in_test(root: S) -> int:
    """`while (1) { T = E; if (C(T)) break; ... }` -> `while (!C((T = E))) { ... }`."""
    n = 0
    for block in [x for x in cs.walk(root) if x.kind == "block"]:
        for k, s in enumerate(block.body):
            if s.kind != "while" or s.text != "1":
                continue
            body = cs.items(s.body[0])
            if len(body) < 2 or body[0].kind != "expr" or body[1].kind != "if" or body[1].orelse is not None:
                continue
            m = ASSIGN.match(body[0].text)
            if not m or not _only(body[1].body[0], "break"):
                continue
            var = m.group("var")
            cond = cs.negate(body[1].text)
            if len(re.findall(rf"\b{re.escape(var)}\b", cond)) != 1:
                continue
            cond = re.sub(rf"\b{re.escape(var)}\b", f"({var} = {m.group('val').strip()})", cond)
            block.body[k] = S("while", cond, [S("block", body=body[2:])])
            n += 1
    return n


def variants(source: str, function: str, limit: int = 8) -> list[tuple[str, str]]:
    """[(label, new source)] of loop spellings for the draft; the first is the canonical form."""
    from solver import rewrite_library
    try:
        begin, stop = rewrite_library._body(source, function)
        root = cs.parse_body(source, begin, stop)
    except Exception:
        return []
    canon, stats = canonical(root)
    out, seen = [], {source}

    def emit(label, tree):
        new = cs.replace_body(source, begin, stop, tree)
        if new not in seen:
            seen.add(new)
            out.append((label, new))

    if stats["goto_loops"] or stats["deduped_returns"] or stats["canonical_loops"]:
        emit(f"loops:canonical(goto={stats['goto_loops']},dup={stats['deduped_returns']})", canon)
    unrot = copy.deepcopy(canon)
    sites = list(_unrotate_sites(unrot))
    for block, k in reversed(sites):
        _unrotate(block, k)
    if sites:
        emit(f"loops:unrotated({len(sites)})", unrot)
    for base_label, base in (("unrotated", unrot), ("canonical", canon)):
        for all_steps in (False, True):
            t2 = copy.deepcopy(base)
            plans = list(_for_plans(t2))
            for plan in sorted(plans, key=lambda x: -x[1]):
                _to_for(*plan, all_steps=all_steps)
            if plans:
                emit(f"loops:{base_label}+for{'-all' if all_steps else ''}({len(plans)})", t2)
    t3 = copy.deepcopy(unrot)
    n = _assign_in_test(t3)
    if n:
        emit(f"loops:assign-in-test({n})", t3)
    return out[:limit]
