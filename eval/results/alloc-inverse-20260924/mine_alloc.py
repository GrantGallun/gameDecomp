"""Mine (register mismatch type, edit type) -> fix rate from the campaign's parent->child edges (PROTOCOL.md).

    python3 mine_alloc.py -> analysis.json
"""
import collections
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import mechanism_roadmap  # noqa: E402
from solver import alignment  # noqa: E402
from tools import n64_corpus  # noqa: E402

HERE = Path(__file__).resolve().parent
DB = Path.home() / "decomp/runs/resume-pipeline-20260908/campaign.sqlite"
REG = re.compile(r"\b(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|fp|ra|gp|sp|k[01])\b")
MIN_N, MIN_F, MIN_RATE, MIN_LIFT = 30, 10, 0.20, 2.0
TYPES = r"(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|char|short|long|float|double|void|struct\s+\w+|[A-Z]\w*)"
DECL = re.compile(r"^(?:register\s+|volatile\s+|static\s+|const\s+)*" + TYPES + r"[\s\*]+\w+(?:\[[^\]]*\])?(?:\s*=.*)?;$")
CAST = re.compile(r"\(\s*(?:unsigned |signed )?" + TYPES + r"\s*\**\s*\)")


def rclass(r):
    return "v" if r.startswith("v") else "a" if r.startswith("a") and r != "at" else "t" if r.startswith("t") or r == "at" \
        else "s" if r.startswith("s") and r not in ("sp",) or r == "fp" else "other"


def residual(diff):
    """(register steps, structural steps, mismatch pair kinds) from a recorded unified diff."""
    if not diff or not diff.lstrip().startswith(("---", "@@")):
        return None
    reg = struct = 0
    kinds = collections.Counter()
    try:
        steps = alignment.align_diff(diff).steps
    except Exception:
        return None
    for step in steps:
        t, c = step.target, step.candidate
        if step.ambiguous or (t is not None and c is not None and t.text == c.text):
            continue
        if t is None or c is None or t.text.split()[0] != c.text.split()[0]:
            struct += 1
            continue
        tr, cr = REG.findall(t.text), REG.findall(c.text)
        tn, cn = REG.sub("R", t.text), REG.sub("R", c.text)
        if tn == cn and len(tr) == len(cr):
            reg += 1
            for a, b in zip(tr, cr):
                if a != b:
                    ka, kb = sorted((rclass(a), rclass(b)))
                    kinds["same-class" if ka == kb else f"{ka}<->{kb}"] += 1
    return reg, struct, kinds


def body_lines(source, name):
    rec = next((r for r in n64_corpus.extract_functions(source or "") if r["name"] == name), None)
    if rec is None:
        return None
    masked = n64_corpus._mask_noncode(str(rec["definition"]))
    body = masked[masked.find("{") + 1:masked.rfind("}")]
    return [re.sub(r"\s+", " ", l).strip() for l in body.splitlines() if l.strip()]


def edit_type(p, c):
    if p is None or c is None or p == c:
        return "none"
    if sorted(p) == sorted(c):
        moved = [a for a, b in zip(p, c) if a != b]
        return "decl_order" if moved and all(DECL.match(l) for l in moved) else "stmt_order"
    sm = collections.Counter(p) - collections.Counter(c)
    sc = collections.Counter(c) - collections.Counter(p)
    removed, added = list(sm.elements()), list(sc.elements())
    pd, cd = sum(bool(DECL.match(l)) for l in p), sum(bool(DECL.match(l)) for l in c)
    kinds = set()
    if len(removed) == 1 and len(added) == 1:
        a, b = removed[0], added[0]
        if sorted(re.findall(r"\w+|\S", a)) == sorted(re.findall(r"\w+|\S", b)) and a != b:
            return "operand_swap"
        if DECL.match(a) and DECL.match(b) and a.split()[-1] == b.split()[-1]:
            if "register" in a + b and a.replace("register ", "") == b.replace("register ", ""):
                return "register_kw"
            return "local_type"
        if CAST.sub("", a) == CAST.sub("", b):
            return "cast"
    if cd == pd + 1:
        kinds.add("temp_intro")
    elif cd == pd - 1:
        kinds.add("temp_remove")
    if all(CAST.sub("", x) in {CAST.sub("", y) for y in added} for x in removed) and removed:
        kinds.add("cast")
    return kinds.pop() if len(kinds) == 1 else "multi" if kinds else "other"


def main():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    att = {}
    for aid, fa, comp, exact, diff, src in db.execute(
            "select id, func_addr, compiled, exact, diff_summary, source_code from attempts"):
        att[aid] = (fa, comp, exact, diff, src)
    names = {a: n for a, n in db.execute("select addr, name from functions")}
    seen, rows = set(), []
    stats = collections.Counter()
    cache = {}

    def res(aid):
        if aid not in cache:
            cache[aid] = residual(att[aid][3])
        return cache[aid]
    for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
        if p not in att or c not in att:
            continue
        pf, pc, pe, pdiff, psrc = att[p]
        cf, cc, ce, cdiff, csrc = att[c]
        if pf != cf or not pc or not cc or pe:
            continue
        rp = res(p)
        if rp is None or rp[0] == 0 or rp[1] > 2:
            continue
        name = names.get(pf, hex(pf))
        key = (name, hashlib.sha1((psrc or "").encode()).hexdigest(), hashlib.sha1((csrc or "").encode()).hexdigest())
        if key in seen:
            continue
        seen.add(key)
        rc = (0, 0, collections.Counter()) if ce else res(c)
        if rc is None:
            stats["child residual unreadable"] += 1
            continue
        mismatch = rp[2].most_common(2)
        mtype = "none" if not mismatch else (mismatch[0][0] if len(mismatch) == 1 or mismatch[0][1] > mismatch[1][1]
                                             else "mixed")
        et = edit_type(body_lines(psrc, name), body_lines(csrc, name))
        fix = rc[0] < rp[0] and rc[1] <= rp[1]
        rows.append({"function": name, "mismatch": mtype, "edit": et, "fix": fix, "exact": bool(ce),
                     "same_diff": (cdiff or "") == (pdiff or "") and not ce, "reg": [rp[0], rc[0]]})
    # controls
    neg = [r for r in rows if r["same_diff"]]
    pos = [r for r in rows if r["exact"]]
    controls = {"negative_same_diff_edges": len(neg), "negative_fixes": sum(r["fix"] for r in neg),
                "positive_exact_edges": len(pos), "positive_counted_as_fix": sum(r["fix"] for r in pos)}
    controls["ok"] = controls["negative_fixes"] == 0 and controls["positive_counted_as_fix"] == controls["positive_exact_edges"]
    by_edit = collections.defaultdict(lambda: [0, 0])
    cells = collections.defaultdict(lambda: {"n": 0, "fix": 0, "exact": 0, "functions": set()})
    for r in rows:
        by_edit[r["edit"]][0] += 1
        by_edit[r["edit"]][1] += r["fix"]
        cell = cells[(r["mismatch"], r["edit"])]
        cell["n"] += 1
        cell["fix"] += r["fix"]
        cell["exact"] += r["exact"]
        cell["functions"].add(r["function"])
    edit_rate = {e: f / n for e, (n, f) in by_edit.items() if n}
    cands = []
    for (m, e), c in cells.items():
        rate = c["fix"] / c["n"]
        if c["n"] >= MIN_N and len(c["functions"]) >= MIN_F and rate >= MIN_RATE and edit_rate.get(e) and \
                rate >= MIN_LIFT * edit_rate[e]:
            cands.append({"mismatch": m, "edit": e, "n": c["n"], "functions": len(c["functions"]),
                          "fix_rate": round(rate, 3), "edit_rate": round(edit_rate[e], 3), "exact": c["exact"]})
    out = {"edges": len(rows), "stats": dict(stats), "controls": controls,
           "mismatch_types": dict(collections.Counter(r["mismatch"] for r in rows)),
           "edit_fix_rates": {e: {"n": n, "fix": f, "rate": round(f / n, 3)} for e, (n, f) in
                              sorted(by_edit.items(), key=lambda kv: -kv[1][0])},
           "rule_candidates": sorted(cands, key=lambda c: -c["fix_rate"]),
           "cells": {f"{m}|{e}": {"n": c["n"], "fix": c["fix"], "functions": len(c["functions"]), "exact": c["exact"]}
                     for (m, e), c in sorted(cells.items(), key=lambda kv: -kv[1]["n"])}}
    (HERE / "analysis.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "cells"}, indent=1))


if __name__ == "__main__":
    main()
