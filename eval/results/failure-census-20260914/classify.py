"""Label each near/close function's aligned non-register differences by mechanical cause.

    python eval/results/failure-census-20260914/classify.py

Reads diffs/*.json (diff_pass.py). Branch targets are masked first: a shifted
offset is a consequence of an insertion elsewhere, not a cause. Writes
classes.json: per-function labels, "single-cause" groups, and label counts.
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRANCH = re.compile(r"^(b\w*|j|jal)$")
RELOC = re.compile(r"%(hi|lo)\(([^)]+)\)")
NUMBER = re.compile(r"(?<![\w$])-?(?:0x[0-9a-fA-F]+|\d+)(?![\w])")
WIDTH = {frozenset(p) for p in (("lb", "lbu"), ("lh", "lhu"), ("lb", "lh"), ("lbu", "lhu"), ("lh", "lw"), ("lhu", "lw"),
                                ("sb", "sh"), ("sh", "sw"), ("lb", "lw"), ("lbu", "lw"), ("sb", "sw"))}
SIGNED = {frozenset(p) for p in (("slt", "sltu"), ("slti", "sltiu"), ("mult", "multu"), ("div", "divu"),
                                 ("sra", "srl"), ("addu", "subu"), ("bltz", "blez"), ("bgez", "bgtz"))}


def split(text):
    parts = text.split(None, 1)
    return parts[0], (parts[1].split(",") if len(parts) > 1 else [])


def masked(text):
    mnemonic, ops = split(text)
    if BRANCH.match(mnemonic) and ops and mnemonic not in ("jr", "jalr"):
        ops = ops[:-1] + ["<target>"]
    return f"{mnemonic} {','.join(ops)}"


def pair_label(want, got):
    wm, wo = split(want)
    gm, go = split(got)
    wr, gr = RELOC.findall(want), RELOC.findall(got)
    if wr and not gr:
        return "literal_where_symbol" if NUMBER.search(",".join(go)) else "relocation_other"
    if gr and not wr:
        return "symbol_where_literal"
    if wr and gr:
        wsym, gsym = wr[0][1], gr[0][1]
        if gsym.startswith(".") and not wsym.startswith("."):
            return "rodata_where_named"
        return "relocation_symbol"
    if wm == gm:
        if len(wo) == len(go) and NUMBER.sub("N", ",".join(wo)) == NUMBER.sub("N", ",".join(go)):
            if wm == "addiu" and wo[:2] == ["sp", "sp"]:
                return "frame_size"
            if any("(sp)" in o for o in wo):
                return "stack_slot"
            return "immediate"
        return "operands_other"
    pair = frozenset((wm, gm))
    if pair in WIDTH:
        return "width"
    if pair in SIGNED:
        return "signedness"
    if BRANCH.match(wm) and BRANCH.match(gm):
        return "branch_kind"
    return f"opcode:{wm}->{gm}"


def blocks(entry):
    diffs = (entry.get("compare") or {}).get("differences", [])
    inserts, deletes, rows = [], [], []
    for d in diffs:
        if "kind" not in d:
            continue                                   # register-only aligned pair
        want = [masked(t) for t in d["target"]]
        got = [masked(t) for t in d["candidate"]]
        if want == got:
            continue                                   # only a branch offset moved
        if d["kind"] == "insert":
            inserts.append((d["index"], got))
        elif d["kind"] == "delete":
            deletes.append((d["index"], want))
        else:
            rows.append((d["index"], want, got))
    return inserts, deletes, rows


def labels(entry):
    compare = entry.get("compare")
    if entry.get("error"):
        return ["bench_error"]
    if not entry.get("compiled"):
        return ["did_not_compile_fresh"]
    if entry.get("exact"):
        return ["already_exact_fresh"]
    if compare is None:
        return ["no_dump"]
    found = []
    inserts, deletes, rows = blocks(entry)
    # Moves: the same instruction run deleted in one place and inserted in another.
    unmatched_deletes = list(deletes)
    for index, got in inserts:
        match = next((d for d in unmatched_deletes if d[1] == got), None)
        if match:
            unmatched_deletes.remove(match)
            found.append("move:" + ("adjacent" if abs(match[0] - index) <= 3 else "far"))
        else:
            found.append("extra:" + "+".join(sorted({split(t)[0] for t in got})))
    for _index, want in unmatched_deletes:
        found.append("missing:" + "+".join(sorted({split(t)[0] for t in want})))
    for _index, want, got in rows:
        if len(want) == len(got):
            found += [pair_label(w, g) for w, g in zip(want, got) if w != g]
        else:
            wm, gm = Counter(split(t)[0] for t in want), Counter(split(t)[0] for t in got)
            extra, missing = gm - wm, wm - gm
            found.append("reshape:-" + "+".join(sorted(missing)) + "/+" + "+".join(sorted(extra)))
    if compare.get("register_instructions"):
        found.append("register")
    if not found:
        found.append("no_instruction_difference:" + (entry.get("boundary_error") or entry.get("certificate_status") or "unknown"))
    return found


def family(label):
    return label.split(":")[0] if not label.startswith("no_instruction") else label


rows = []
for path in sorted((HERE / "diffs").glob("*.json")):
    entry = json.loads(path.read_text())
    found = labels(entry)
    rows.append({"function": entry["function"], "score": entry.get("score"), "instructions": entry.get("instructions"),
                 "total_faults": entry.get("total_faults"), "labels": found,
                 "families": sorted({family(l) for l in found})})

label_counts = Counter(l for r in rows for l in set(r["labels"]))
family_presence = Counter(f for r in rows for f in r["families"])
single = defaultdict(list)
for r in rows:
    causes = [f for f in r["families"] if f != "register"]
    key = "+".join(causes) if len(causes) <= 1 else "multi"
    if len(causes) == 1:
        key = causes[0] + ("+register" if "register" in r["families"] else "")
    elif not causes:
        key = "register_only"
    single[key].append(r["function"])
report = {"functions": len(rows),
          "single_cause_groups": {k: len(v) for k, v in sorted(single.items(), key=lambda kv: -len(kv[1]))},
          "family_presence": dict(family_presence.most_common()),
          "label_presence": dict(label_counts.most_common(40)),
          "groups": dict(single), "rows": rows}
(HERE / "classes.json").write_text(json.dumps(report, indent=1))
print(json.dumps({k: report[k] for k in ("functions", "single_cause_groups", "family_presence", "label_presence")}, indent=1))
