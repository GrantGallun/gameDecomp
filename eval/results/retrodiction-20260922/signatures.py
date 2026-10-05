"""Blind potential analysis with four mechanisms hidden (see PREREGISTRATION.md). No compiles."""
import collections
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import diffrepair  # noqa: E402

SOURCES = [Path.home() / "decomp/experiments/population-transfer-20260922/rows",
           Path.home() / "decomp/experiments/population-transfer-20260922/stage2/rows"]
HIDDEN = {"owner:drop_mask", "owner:layout", "owner:per_object_layout", "owner:global_load_signedness"}
TARGETS = {"extra:andi": "owner:drop_mask", "field:offset": "owner:layout",
           "opcode:lb/lbu": "owner:global_load_signedness", "opcode:lh/lhu": "owner:global_load_signedness"}
REG = re.compile(r"^\$?(zero|at|v[01]|a[0-3]|t[0-9]|s[0-8]|k[01]|gp|sp|fp|ra|f\d+)$")
MEMOP = re.compile(r"^(?P<off>[^()]*)\((?P<base>\$?\w+)\)$")
RELOC = re.compile(r"%(?:hi|lo)\((?P<sym>[A-Za-z_]\w*)")
LOADSTORE = {"lb", "lbu", "lh", "lhu", "lw", "sb", "sh", "sw", "lwc1", "swc1"}
BRANCH = re.compile(r"^(b\w*|j|jal)$")


def parse(instr):
    parts = instr.split(None, 1)
    return parts[0], [o.strip() for o in (parts[1].split(",") if len(parts) > 1 else [])]


def fields(op, operands):
    out = []
    for o in operands:
        m = MEMOP.match(o)
        if m:
            out += [("offset", RELOC.search(m["off"]).group("sym") if RELOC.search(m["off"]) else m["off"]), ("register", m["base"])]
        elif RELOC.search(o):
            out.append(("symbol", RELOC.search(o).group("sym")))
        elif REG.match(o):
            out.append(("register", o))
        elif BRANCH.match(op):
            out.append(("branch", o))
        else:
            out.append(("immediate", o))
    return out


def candidate_stream_lines(diff):
    """Candidate-stream index (as solver.alignment.streams builds it) -> normalized-dump line number."""
    out, line = [], 0
    for raw in diff.splitlines():
        h = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw)
        if h:
            line = int(h.group(1))
            continue
        if not raw or raw.startswith(("---", "+++")):
            continue
        if raw[0] in " +":
            if raw[1:].strip():                     # same skip rule as alignment.streams
                out.append(line)
            line += 1
    return out


def signatures(diff, attribution):
    """[(signature, stated_expressible, c_line or None, target_text, candidate_text)] for one residual.

    v2: the general block-aware aligner (solver.alignment.align_diff); v1 used diffrepair.aligned_pairs,
    which by design returns only offset-differing pairs, so every other pair fell through as unpaired.
    """
    from solver import alignment
    lines = candidate_stream_lines(diff)
    attr = {r["normalized_line"]: r.get("candidate_line") for r in (attribution or {}).get("instructions", [])} \
        if (attribution or {}).get("status") == "verified" else {}

    def locate(insn):
        return attr.get(lines[insn.index]) if insn is not None and insn.index < len(lines) else None

    out = []
    for step in alignment.align_diff(diff).steps:
        t, c = step.target, step.candidate
        if step.ambiguous or (t is not None and c is not None and t.text == c.text):
            continue
        if t is not None and c is not None:
            if t.opcode != c.opcode:
                pair = "/".join(sorted((t.opcode, c.opcode)))
                out.append((f"opcode:{pair}", t.opcode in LOADSTORE and c.opcode in LOADSTORE, locate(c), t.text, c.text))
                continue
            tf, cf = fields(t.opcode, list(t.operands)), fields(c.opcode, list(c.operands))
            diffs = {k for (k, a), (_k2, b) in zip(tf, cf) if a != b} if len(tf) == len(cf) else {"shape"}
            if len(diffs) == 1:
                kind = diffs.pop()
                out.append((f"field:{kind}", kind in {"offset", "immediate", "symbol"}, locate(c), t.text, c.text))
        elif c is not None:
            out.append((f"extra:{c.opcode}", True, locate(c), None, c.text))
        elif t is not None:
            out.append((f"missing:{t.opcode}", False, None, t.text, None))
    return out


def load():
    for directory in SOURCES:
        for path in sorted(directory.glob("*.json")):
            row = json.loads(path.read_text())
            if row.get("world"):
                yield row["function"], row["arm"], json.loads(Path(row["world"]).read_text())["world"]


def main():
    demand = collections.defaultdict(set)
    locatable = collections.Counter()
    instances = collections.Counter()
    stated = {}
    covered = collections.defaultdict(set)
    for function, _arm, world in load():
        nodes = {n["id"]: n for n in world["nodes"]}

        def hidden(n):
            while n["parent"] is not None:
                if n["family"] in HIDDEN:
                    return True
                n = nodes[n["parent"]]
            return False
        sigs = {}
        for n in world["nodes"]:
            v = n["verdict"]
            if not v["compiled"] or v["exact"] or hidden(n):
                continue
            found = signatures(v.get("diff") or "", v.get("source_attribution"))
            sigs[n["id"]] = collections.Counter(s for s, *_ in found)
            for s, expressible, line, *_ in found:
                demand[s].add(function)
                stated[s] = expressible
                instances[s] += 1
                locatable[s] += line is not None
        for n in world["nodes"]:
            if n["parent"] in sigs and not hidden(n) and n["verdict"]["compiled"]:
                after = sigs.get(n["id"], collections.Counter()) if not n["verdict"]["exact"] else collections.Counter()
                for s, k in sigs[n["parent"]].items():
                    if after[s] < k:
                        covered[s].add(function)
    rows = []
    for s, fns in demand.items():
        loc = locatable[s] / instances[s] if instances[s] else 0
        uncovered = len(fns - covered[s])
        potential = uncovered * loc if stated[s] else 0.0
        rows.append({"signature": s, "functions": len(fns), "covered_functions": len(covered[s] & fns),
                     "uncovered_functions": uncovered, "stated_expressible": stated[s], "locatable": round(loc, 3),
                     "potential": round(potential, 2)})
    eligible = sorted((r for r in rows if r["functions"] >= 5), key=lambda r: -r["potential"])
    for i, r in enumerate(eligible, 1):
        r["rank"] = i
    quartile = max(1, len(eligible) // 4)
    verdict = {s: {"rank": next((r["rank"] for r in eligible if r["signature"] == s), None), "of": len(eligible),
                   "top_quartile": any(r["signature"] == s and r["rank"] <= quartile for r in eligible),
                   "hidden_mechanism": m} for s, m in TARGETS.items()}
    (HERE / "signatures.json").write_text(json.dumps({"ranked": eligible, "hidden_targets": verdict}, indent=1))
    print(f"{'rank':>4} {'signature':22} {'fns':>4} {'covered':>7} {'uncov':>5} {'stated':>6} {'locat':>5} {'potential':>9}")
    for r in eligible[:22]:
        mark = "  <- hidden: " + TARGETS[r["signature"]] if r["signature"] in TARGETS else ""
        print(f"{r['rank']:4} {r['signature']:22} {r['functions']:4} {r['covered_functions']:7} {r['uncovered_functions']:5} "
              f"{str(r['stated_expressible']):>6} {r['locatable']:5} {r['potential']:9}{mark}")
    print(json.dumps(verdict, indent=1))


if __name__ == "__main__":
    main()
