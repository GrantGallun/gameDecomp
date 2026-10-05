"""Register steering from confirmed IDO 5.3 allocator rules (catalog uopt53-*). Traced compiles; candidates to probes.

For each diagnosed wrong range (census.json):
  map ranges to C variables by intervention: add `if (!v);` after v's first assignment; the range (identified by
    isvar kind and frame offset) whose save rises by exactly 1 is v's (uopt53-save-counts-reads,
    uopt53-empty-test-steers-priority)
  constrained pair that swapped colours: raise x above y. save_x = adjsave_x * units(span_x); the smallest k with
    (save_x + k) / units(span_x) > adjsave_y, or equal with x's live-range number lower (uopt53-colouring-order,
    uopt53-priority-block-units). Emit k x `if (!x);`.
  unconstrained range wanting a colour an earlier-coloured range took: unconstrained ranges are coloured in
    live-range (first-store) order, so move x's first assignment to just before y's.
  ugen_temp (the target never makes the value a variable): inline x's single assignment into its reads.
"""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval.allocator_rules import span, units  # noqa: E402
from solver import regalloc_mutations, uopt_diagnosis, uopt_trace  # noqa: E402

HERE = Path(__file__).resolve().parent
RUN = Path.home() / "decomp/experiments/gated-population-20260923"
TRACE_CC = Path.home() / "decomp/tools-src/ido-trace/cc"
KEYWORDS = {"return", "if", "else", "for", "while", "do", "switch", "case", "goto", "break", "continue", "sizeof"}


def variables(source, function):
    begin, stop = regalloc_mutations._body(source, function)
    head = source[:begin]
    params = re.findall(r"([A-Za-z_]\w*)\s*(?:,|\)\s*\{?\s*$)", head[head.rfind(function):]) if function in head else []
    body = source[begin:stop]
    locals_ = [m.group(2) for m in re.finditer(
        r"^[ \t]*((?:[A-Za-z_]\w*[ \t]+)+\**[ \t]*)([A-Za-z_]\w*)[ \t]*(?:\[[^\]]*\])?[ \t]*(?:=[^;\n]*)?;", body, re.M)
        if m.group(1).split()[0] not in KEYWORDS]
    names = [n for n in dict.fromkeys(params + locals_) if n != function and n not in KEYWORDS]
    return [n for n in names if len(re.findall(rf"\b{n}\b", body)) >= 2]


def insert_after_first_assignment(source, function, name, text):
    begin, stop = regalloc_mutations._body(source, function)
    body = source[begin:stop]
    m = re.search(rf"^[^\n]*\b{name}\s*=(?!=)[^;\n]*;[^\n]*$", body, re.M)
    if not m:                                  # a parameter: after the declarations at the top of the body
        decls = list(re.finditer(r"^[ \t]*(?:[A-Za-z_]\w*[ \t]+)+\**[ \t]*[A-Za-z_]\w*[^;\n]*;[ \t]*$", body, re.M))
        at = decls[-1].end() if decls and decls[-1].start() < 400 else body.find("\n") + 1 - 1
    else:
        at = m.end()
    return source[:begin + at] + "\n" + text + source[begin + at:]


def traced(name, source):
    repo = RUN / "ws" / name / "gated"
    texts = uopt_diagnosis.traced_compile(repo / "nonmatchings" / name, repo, source, TRACE_CC, name)
    return uopt_trace.join(texts["level5"], texts["level6"]).get(name) if texts else None


def identity(r):
    return (r.kind, r.offset)


WORD = re.compile(r"^(?:s32|u32|int|unsigned|long|f32|float)$")


def mapping(name, source, base_proc):
    """Locals by declaration order: frame offset -4, -8, ... (catalog uopt53-local-offsets, H7 confirmed on 3
    orders). Only while every local so far is one word (scalar word or pointer); stop at the first that is not."""
    begin, stop = regalloc_mutations._body(source, name)
    found, offset = {}, -4
    for m in re.finditer(r"^[ \t]*((?:[A-Za-z_]\w*[ \t]+)+)(\**)[ \t]*([A-Za-z_]\w*)[ \t]*(\[)?[^;\n]*;",
                         source[begin:stop], re.M):
        words = m.group(1).split()
        if words[0] in KEYWORDS or "(" in m.group(0).split("=")[0]:
            continue
        if m.group(4) or not (m.group(2) or WORD.match(words[-1])):
            break
        found[("M", offset)] = m.group(3)
        offset -= 4
    return found


def assignment_sites(source, function, x):
    begin, stop = regalloc_mutations._body(source, function)
    return [begin + m.end() for m in re.finditer(rf"^[^\n]*\b{x}\s*(?:[-+*/|&^]?=)(?!=)[^;\n]*;[^\n]*$",
                                                 source[begin:stop], re.M)]


def insert_at(source, at, text):
    return source[:at] + "\n" + text + source[at:]


def ugen_inline(source, function, x):
    begin, stop = regalloc_mutations._body(source, function)
    body = source[begin:stop]
    assigns = list(re.finditer(rf"^[ \t]*{x}\s*=(?!=)\s*(?P<e>[^;\n]+);[ \t]*\n", body, re.M))
    decl = re.search(rf"^[ \t]*(?:[A-Za-z_]\w*[ \t]+)+\**[ \t]*{x}[ \t]*;[ \t]*\n", body, re.M)
    if len(assigns) != 1 or not decl:
        return None
    e = assigns[0].group("e").strip()
    text = body[:assigns[0].start()] + body[assigns[0].end():]
    text = text.replace(decl.group(0), "", 1)
    text = re.sub(rf"\b{x}\b", lambda _m: f"({e})", text)
    return source[:begin] + text + source[stop:]


def move_first_assignment(source, function, x, before):
    begin, stop = regalloc_mutations._body(source, function)
    body = source[begin:stop]
    mx = re.search(rf"^[ \t]*{x}\s*=(?!=)[^;\n]*;[ \t]*\n", body, re.M)
    my = re.search(rf"^[ \t]*{before}\s*=(?!=)[^;\n]*;[ \t]*\n", body, re.M)
    if not mx or not my or mx.start() < my.start():
        return None
    text = body[:mx.start()] + body[mx.end():]
    return source[:begin] + text[:my.start()] + mx.group(0) + text[my.start():] + source[stop:]


def main():
    census = json.loads((HERE / "census.json").read_text())
    probes, notes = [], []
    for c in census:
        wrong = c.get("wrong_ranges") or []
        if not wrong:
            continue
        name, source = c["function"], c["best_source"]
        proc = uopt_trace.join(c["trace"]["level5"], c["trace"]["level6"])[name]
        names = mapping(name, source, proc)
        var = {lr: names.get(identity(r)) for lr, r in proc.ranges.items()}
        order = [d.piece for d in proc.decisions if d.outcome != "not_colored"]
        by_actual = {w["actual"]: w for w in wrong}
        made = []
        for w in wrong:
            x = var.get(w["lr"])
            if w["class"] == "ugen_temp" and x:
                made.append(("ugen_inline", x, ugen_inline(source, name, x)))
            partner = by_actual.get(w.get("desired"))
            if partner and partner.get("desired") == w["actual"] and w["outcome"] == partner["outcome"] == "constrained" \
                    and x and w["lr"] != partner["lr"]:
                rx, ry = proc.ranges[w["lr"]], proc.ranges[partner["lr"]]
                ux, save = units(span(rx)), rx.adjsave * units(span(rx))
                k = next(k for k in range(1, 400) if (save + k) / ux > ry.adjsave
                         or ((save + k) / ux == ry.adjsave and w["lr"] < partner["lr"]))
                # After each assignment of x: right after a constant one the read is folded away (H6).
                for site in assignment_sites(source, name, x):
                    made.append((f"raise+{k}@{site}", x, insert_at(source, site, "\n".join([f"    if (!{x});"] * k))))
            if w["outcome"] == "unconstrained" and x and w.get("desired"):
                holder = next((lr for lr in order[:order.index(w["lr"])] if lr in proc.ranges
                               and uopt_diagnosis.uopt_attribution.REGISTER_NAMES[
                                   uopt_diagnosis.uopt_attribution.colour_register(proc.ranges[lr].color)] == w["desired"]),
                              None) if w["lr"] in order else None
                y = var.get(holder)
                if y:
                    made.append(("store_first", f"{x}<{y}", move_first_assignment(source, name, x, y)))
        notes.append({"function": name, "mapped": len(names), "variables": variables(source, name),
                      "candidates": [(kind, who, cand is not None) for kind, who, cand in made]})
        for i, (kind, who, cand) in enumerate(made):
            if cand and cand != source:
                probes.append({"function": name, "label": f"steer:{kind}:{who}:{i}", "source": cand})
        print(json.dumps(notes[-1]), flush=True)
    (HERE / "probes-steer.json").write_text(json.dumps(probes, indent=1))
    (HERE / "steer-notes.json").write_text(json.dumps(notes, indent=1))
    print("candidates:", len(probes))


if __name__ == "__main__":
    main()
