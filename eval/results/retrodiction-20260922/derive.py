"""The single generic mechanism derived from potential: put the diff's stated fix at the attributed C line.

Rules, all from PREREGISTRATION.md and none from the hidden mechanisms' code:
  field:offset / field:immediate   replace a literal on that line equal to the candidate's value with the target's
  field:symbol                     replace the candidate's symbol on that line with the target's
  opcode:<load/store pair>         retype with the ISA->C map (lb s8, lbu u8, lh s16, lhu u16, lw s32): the type
                                   token on that line, else the declaration of an identifier used on it
  extra:<op>                       delete that instruction's operator with its own immediate on that line
                                   (andi `& k`, sll `<< k`, sra/srl `>> k`, addiu `+ k`)
Applied to every parent where a hidden mechanism improved or reached exact; candidates go to probes-derived.json.
"""
import collections
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from signatures import HIDDEN, TARGETS, load, signatures  # noqa: E402

CTYPE = {"lb": "s8", "lbu": "u8", "lh": "s16", "lhu": "u16", "lw": "s32", "sb": "s8", "sh": "s16", "sw": "s32"}
OPERATOR = {"andi": "&", "sll": "<<", "sra": ">>", "srl": ">>", "addiu": "+"}
CAP = 16


def number_forms(value):
    return {f"0x{value:X}", f"0x{value:x}", str(value)} if value >= 0 else {str(value), f"-0x{-value:x}", f"-0x{-value:X}"}


def to_int(text):
    try:
        return int(text, 0)
    except ValueError:
        return None


def replace_on_line(source, line, pattern, repl):
    lines = source.split("\n")
    if not 1 <= line <= len(lines):
        return None
    new, n = re.subn(pattern, repl, lines[line - 1], count=1)
    if not n:
        return None
    lines[line - 1] = new
    return "\n".join(lines)


def last_operand(text):
    return text.split(",")[-1].strip() if text and "," in text else None


def derive(source, sig, line, target, cand):
    if line is None:
        return []
    kind = sig.split(":", 1)
    out = []
    if sig in ("field:offset", "field:immediate"):
        grab = lambda t: re.search(r"(-?(?:0x)?[0-9a-fA-F]+)\(", t) if sig == "field:offset" else None
        tv = to_int(grab(target).group(1)) if grab(target) else to_int(last_operand(target) or "")
        cv = to_int(grab(cand).group(1)) if grab(cand) else to_int(last_operand(cand) or "")
        if tv is None or cv is None:
            return []
        for form in number_forms(cv):
            hexa = form.lower().startswith(("0x", "-0x"))
            new = (f"0x{tv:X}" if tv >= 0 else f"-0x{-tv:X}") if hexa else str(tv)
            s = replace_on_line(source, line, rf"(?<![\w.]){re.escape(form)}(?![\w.])", new)
            if s:
                out.append(s)
    elif sig == "field:symbol":
        ts = re.search(r"%(?:hi|lo)\((\w+)", target or "")
        cs = re.search(r"%(?:hi|lo)\((\w+)", cand or "")
        if ts and cs:
            s = replace_on_line(source, line, rf"\b{cs.group(1)}\b", ts.group(1))
            if s:
                out.append(s)
    elif kind[0] == "opcode":
        t_op, c_op = target.split()[0], cand.split()[0]
        if t_op in CTYPE and c_op in CTYPE and CTYPE[t_op] != CTYPE[c_op]:
            want, have = CTYPE[t_op], CTYPE[c_op]
            s = replace_on_line(source, line, rf"\b{have}\b", want)
            if s:
                out.append(s)
            else:                                           # retype a declaration of something used on the line
                used = set(re.findall(r"\b[A-Za-z_]\w*\b", source.split("\n")[line - 1]))
                for name in sorted(used):
                    m = re.search(rf"\b{have}(\s*\*?\s*){name}\b", source)
                    if m:
                        out.append(source[:m.start()] + want + m.group(1) + name + source[m.end():])
    elif kind[0] == "extra":
        op = kind[1]
        imm = to_int(last_operand(cand) or "")
        if op in OPERATOR and imm is not None:
            for form in number_forms(imm):
                s = replace_on_line(source, line, rf"\s*{re.escape(OPERATOR[op])}\s*{re.escape(form)}\b", "")
                if s:
                    out.append(s)
    return out


def main():
    parents, probes = [], []
    for function, _arm, world in load():
        nodes = {n["id"]: n for n in world["nodes"]}
        for n in world["nodes"]:
            if n["parent"] is None or n["family"] not in HIDDEN:
                continue
            p = nodes[n["parent"]]
            good = n["verdict"]["exact"] or (n["verdict"]["compiled"] and n["verdict"]["score"] > p["verdict"]["score"])
            if good and p["verdict"]["compiled"]:
                parents.append((function, n["family"], p, n))
    seen = set()
    per_parent = []
    for function, family, parent, child in parents:
        key = (function, parent["source_sha256"])
        found = signatures(parent["verdict"].get("diff") or "", parent["verdict"].get("source_attribution"))
        own = {s for s, m in TARGETS.items() if m == family or (family == "owner:per_object_layout" and m == "owner:layout")}
        ordered = sorted(found, key=lambda f: f[0] not in own)          # the residual class first, then the rest
        cands, from_own = [], 0
        for sig, expressible, line, target, cand in ordered:
            if not expressible:
                continue
            for s in derive(parent["source"], sig, line, target, cand):
                if s != parent["source"] and s not in cands and len(cands) < CAP:
                    cands.append(s)
                    from_own += sig in own
        per_parent.append({"function": function, "hidden": family, "parent_sha": parent["source_sha256"],
                           "parent_score": parent["verdict"]["score"], "hidden_child_score": child["verdict"]["score"],
                           "hidden_child_exact": child["verdict"]["exact"], "candidates": len(cands),
                           "from_own_residual": from_own,
                           "reproduces_hidden_source": child["source"] in cands})
        if key not in seen:
            seen.add(key)
            for i, s in enumerate(cands):
                probes.append({"function": function, "label": f"derived:{family}:{parent['source_sha256'][:8]}:{i}", "source": s})
    (HERE / "derive.json").write_text(json.dumps(per_parent, indent=1))
    (HERE / "probes-derived.json").write_text(json.dumps(probes, indent=1))
    by = collections.defaultdict(lambda: collections.Counter())
    for r in per_parent:
        b = by[r["hidden"]]
        b["parents"] += 1
        b["any_candidate"] += r["candidates"] > 0
        b["candidate_from_own_residual"] += r["from_own_residual"] > 0
        b["reproduces_hidden_source_exactly"] += r["reproduces_hidden_source"]
    print(json.dumps({k: dict(v) for k, v in by.items()}, indent=1))
    print("candidates to compile:", len(probes))


if __name__ == "__main__":
    main()
