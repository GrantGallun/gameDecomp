"""Replay IDO's register colouring from the uopt model; count exact registers.

`eval.uopt_validate` measured the model PAIRWISE (higher save -> lower register).
A greedy colourer promises something else: each web, in priority order, takes
the lowest pool index its already-coloured NEIGHBOURS leave free. So this
replays the colouring and asks the question a repair search needs answered --
does the model put each web in EXACTLY the register IDO chose? -- over every
target dump (IDO's own output, matched or not; no source, no reference C).

WEBS ARE BUILT ALONG CONTROL FLOW, not linear text. The first version split a
register's history at each textual redefinition. Half of its "webs" then had a
single occurrence, their live ranges were empty, and replay piled 64% of them
into v0/v1 while IDO spreads values across v0-t5 -- the replay was measuring its
own fragmentation. `solver/liveness.py` records the same root cause behind the
save model's 50-58% pairwise result: "webs rebuilt from POST-allocation assembly
are not uopt's webs." Here, webs are unions of reaching definitions that meet at
a use, liveness is solved over `solver.cfg`, and interference is added at every
definition against everything live -- the standard construction.

Two input facts this depends on, both easy to get silently wrong:

* Dumps name branch targets as hex byte offsets with no labels. `cfg.build`
  resolves those ONLY behind an explicit `# MIPS_DIFF_NUMERIC_BRANCH_BASE`
  marker; without it every branch has an unknown successor and every function
  collapses to straight lines. `graph_for` adds the marker.
* The first operand of a load is only a write when it IS a pool register:
  `lwc1 f0,0x4(a0)` writes f0 and reads a0. And `mult`/`div` write hi/lo, not
  their first operand. `uopt.WRITES_FIRST` gets both wrong when paired with
  "first register found", so def/use is decided per operand here.

THE ERROR IS DECOMPOSED so one number cannot hide which half is wrong:

    model   priority = descending save (the model under test)
    oracle  priority = ascending ACTUAL colour. If even this cannot reproduce
            IDO, the graph/pool reconstruction is wrong and no save formula
            can fix it.
    chrono  priority = construction order (save ignored)
    random  mean over seeded shuffles -- the floor real signal must beat

EXCLUDED FROM SCORING (still present as fixed colours where they are live):
incoming parameters, call arguments and results, the return value at `jr ra`,
dead definitions (call clobbers included), and frame saves/restores of callee-
saved registers, which are not values at all. Functions with an unresolved
successor (jump tables) are skipped and counted, never guessed.

    python3 -m eval.uopt_replay --status MAP.json \\
        --out eval/results/uopt-replay-YYYYMMDD/report.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from solver import cfg, uopt

POOL = set(uopt.COLOR_POOL)
CALLEE = [r for r in uopt.COLOR_POOL if r.startswith("s")]
CALLER_POOL = [r for r in uopt.COLOR_POOL if not r.startswith("s")]
CALL_OPS = {"jal", "jalr", "bal"}
NUMERIC_MARKER = "# MIPS_DIFF_NUMERIC_BRANCH_BASE 0x0"
NO_WRITE = {"mult", "multu", "div", "divu", "dmult", "dmultu", "ddiv", "ddivu"}
FRAME_OPS = {"sw", "lw", "sd", "ld"}
SP_SLOT = re.compile(r"\(sp\)$")


def _reg(token: str) -> str | None:
    found = uopt.REG.findall(token)
    return uopt.normalize(found[0][0] or found[0][1]) if found else None


def _pool_regs(token: str) -> list[str]:
    return [r for a, b in uopt.REG.findall(token) if (r := uopt.normalize(a or b)) in POOL]


def graph_for(asm: str) -> cfg.ControlFlowGraph:
    return cfg.build(asm.rstrip() + "\n" + NUMERIC_MARKER + "\n")


def _is_frame(insn: cfg.Instruction) -> bool:
    return (insn.opcode in FRAME_OPS and len(insn.operands) == 2
            and _reg(insn.operands[0]) in CALLEE and bool(SP_SLOT.search(insn.operands[1])))


def local_def_use(insn: cfg.Instruction) -> tuple[list[str], list[str]]:
    """(defined, used) pool registers, decided per operand -- see module note."""
    if _is_frame(insn) or not insn.operands:
        return [], []
    if insn.opcode in CALL_OPS:
        return [], []                       # handled with ABI context below
    if cfg.is_control_transfer(insn.opcode):
        # The branch TARGET is a hex byte offset, and `a0`/`a4`/`a8` are valid
        # offsets: reading it as a register would invent an argument use.
        target = insn.target
        tokens = [t for t in insn.operands if t != target] if target else list(insn.operands)
        return [], [r for tok in tokens for r in _pool_regs(tok)]
    first = insn.operands[0].strip()
    writes = bool(uopt.WRITES_FIRST.match(insn.opcode)) and insn.opcode not in NO_WRITE
    plain = re.fullmatch(r"\$?(\w+)", first)
    if writes and plain:
        written = uopt.normalize(plain.group(1))
        defs = [written] if written in POOL else []     # f0, at, t6-t9: not ours
        uses = [r for tok in insn.operands[1:] for r in _pool_regs(tok)]
        return defs, uses
    return [], [r for tok in insn.operands for r in _pool_regs(tok)]


@dataclass
class Component:
    register: str
    defs: set = field(default_factory=set)        # def ids
    def_lines: list = field(default_factory=list)
    use_lines: list = field(default_factory=list)
    kinds: set = field(default_factory=set)       # entry / call / plain
    arg_use: bool = False
    return_use: bool = False
    crosses_call: bool = False


class _UnionFind:
    def __init__(self):
        self.parent: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def analyse(asm: str):
    """Webs, interference and ABI facts from control flow. None if unresolved."""
    graph = graph_for(asm)
    if graph.entry is None or not graph.blocks:
        return None
    if any(b.unknown_successor for b in graph.blocks.values()):
        return "unresolved"
    insns = graph.instructions

    # ---- definitions: fixed before anything else, uses depend on them ----
    def_info: list[tuple[int, str, str]] = []        # id -> (line, reg, kind)
    defs_at: dict[int, list[int]] = defaultdict(list)
    entry_ids = {}
    for r in uopt.COLOR_POOL:
        entry_ids[r] = len(def_info)
        def_info.append((-1, r, "entry"))
    for insn in insns:
        if insn.opcode in CALL_OPS:
            names, kind = CALLER_POOL, "call"
        else:
            names, kind = local_def_use(insn)[0], "plain"
        for r in names:
            defs_at[insn.index].append(len(def_info))
            def_info.append((insn.index, r, kind))

    # ---- reaching definitions over blocks ----
    entry_state = {r: frozenset({entry_ids[r]}) for r in uopt.COLOR_POOL}
    empty = {r: frozenset() for r in uopt.COLOR_POOL}
    block_in = {b: dict(empty) for b in graph.blocks}
    block_in[graph.entry] = dict(entry_state)
    order = graph.reverse_postorder()
    preds = {b: graph.blocks[b].predecessors for b in graph.blocks}

    def transfer(block, state):
        state = dict(state)
        for insn in block.instructions:
            for d in defs_at.get(insn.index, ()):
                state[def_info[d][1]] = frozenset({d})
        return state

    block_out = {b: transfer(graph.blocks[b], block_in[b]) for b in graph.blocks}
    for _ in range(200):
        changed = False
        for b in order:
            if b == graph.entry:
                incoming = dict(entry_state)
                for p in preds[b]:
                    for r in uopt.COLOR_POOL:
                        incoming[r] = incoming[r] | block_out[p][r]
            else:
                incoming = {r: frozenset().union(*(block_out[p][r] for p in preds[b]))
                            if preds[b] else frozenset() for r in uopt.COLOR_POOL}
            if incoming != block_in[b]:
                block_in[b] = incoming
                block_out[b] = transfer(graph.blocks[b], incoming)
                changed = True
        if not changed:
            break

    # ---- per-instruction reaching state and final use table ----
    before: dict[int, dict] = {}
    uses_at: dict[int, list[str]] = {}
    arg_lines: dict[int, set] = defaultdict(set)
    return_lines: set[int] = set()
    for b, block in graph.blocks.items():
        state = dict(block_in[b])
        for insn in block.instructions:
            before[insn.index] = state
            if insn.opcode in CALL_OPS:
                # An argument register holds a value set up for THIS call when a
                # non-entry, non-call definition reaches it.
                uses = [r for r in uopt.ARG_REGS
                        if any(def_info[d][2] == "plain" for d in state[r])]
                arg_lines[insn.index] = set(uses)
            elif insn.opcode == "jr" and insn.operands \
                    and insn.operands[0].strip() in ("ra", "$ra", "$31"):
                # Compared as text: uopt.REG only knows v/a/t/s names, so `ra`
                # never matched and every return value went unmarked.
                uses = [r for r in uopt.RET_REGS
                        if any(def_info[d][2] != "entry" for d in state[r])]
                return_lines.add(insn.index)
            else:
                uses = local_def_use(insn)[1]
            uses_at[insn.index] = uses
            new = dict(state)
            for d in defs_at.get(insn.index, ()):
                new[def_info[d][1]] = frozenset({d})
            state = new

    # ---- liveness (instruction level, backward to fixpoint) ----
    live_in_block = {b: frozenset() for b in graph.blocks}
    live_out: dict[int, frozenset] = {}
    for _ in range(200):
        changed = False
        for b in reversed(order):
            block = graph.blocks[b]
            out = frozenset().union(*(live_in_block[s] for s in block.successors)) \
                if block.successors else frozenset()
            cur = set(out)
            for insn in reversed(block.instructions):
                live_out[insn.index] = frozenset(cur)
                cur -= {def_info[d][1] for d in defs_at.get(insn.index, ())}
                cur |= set(uses_at[insn.index])
            if frozenset(cur) != live_in_block[b]:
                live_in_block[b] = frozenset(cur)
                changed = True
        if not changed:
            break

    # ---- webs: definitions that reach a common use are one web ----
    uf = _UnionFind()
    for d in range(len(def_info)):
        uf.find(d)
    use_records = []
    for i, regs in uses_at.items():
        for r in regs:
            ds = sorted(before[i][r])
            for d in ds[1:]:
                uf.union(ds[0], d)
            if ds:
                use_records.append((ds[0], i, r))

    comps: dict[int, Component] = {}

    def comp(d: int) -> Component:
        root = uf.find(d)
        if root not in comps:
            comps[root] = Component(register=def_info[d][1])
        return comps[root]

    for d, (line, r, kind) in enumerate(def_info):
        c = comp(d)
        c.defs.add(d)
        c.kinds.add(kind)
        if line >= 0:
            c.def_lines.append(line)
    for d, i, r in use_records:
        c = comp(d)
        c.use_lines.append(i)
        if r in arg_lines.get(i, ()):
            c.arg_use = True
        if i in return_lines:
            c.return_use = True

    # ---- interference at definitions; call crossing ----
    adjacency: dict[int, set] = defaultdict(set)
    for i in before:
        after = dict(before[i])
        for d in defs_at.get(i, ()):
            after[def_info[d][1]] = frozenset({d})
        for d in defs_at.get(i, ()):
            here = uf.find(d)
            for s in live_out.get(i, ()):
                if s == def_info[d][1]:
                    continue
                for sd in after[s]:
                    other = uf.find(sd)
                    if other != here:
                        adjacency[here].add(other)
                        adjacency[other].add(here)
        if insns[i].opcode in CALL_OPS:
            clobbered = set(CALLER_POOL)
            for s in live_out.get(i, ()):
                if s in clobbered:
                    continue
                for sd in before[i][s]:
                    comp(sd).crosses_call = True
    live_at_entry = live_in_block[graph.entry]
    entry_roots = [uf.find(entry_ids[r]) for r in live_at_entry]
    for a in entry_roots:
        for b in entry_roots:
            if a != b:
                adjacency[a].add(b)

    # ---- block occupancy, for Chow-Hennessy block-granular live ranges ----
    # uopt descends from priority-based colouring, whose live range is a SET OF
    # BASIC BLOCKS: a value live anywhere in a block holds its register for the
    # whole block. Kept alongside the instruction-level graph so the two
    # hypotheses are measured side by side rather than one being assumed.
    occupancy: dict[int, set] = defaultdict(set)
    block_of = graph.instruction_to_block
    for b in graph.blocks:
        for r in live_in_block[b]:
            for d in block_in[b][r]:
                occupancy[uf.find(d)].add(b)
    for root, c in comps.items():
        for line in c.def_lines + c.use_lines:
            if line in block_of:
                occupancy[root].add(block_of[line])
    by_block: dict[int, list] = defaultdict(list)
    for root, blocks in occupancy.items():
        for b in blocks:
            by_block[b].append(root)
    block_adjacency: dict[int, set] = defaultdict(set)
    for members in by_block.values():
        for a in members:
            for b in members:
                if a != b:
                    block_adjacency[a].add(b)

    return {"components": comps, "adjacency": adjacency,
            "block_adjacency": block_adjacency, "insns": insns}


def classify(c: Component) -> str:
    """scored | abi | dead | livein -- what this web tells us about uopt."""
    if not c.use_lines:
        return "dead"                       # includes unused entry values
    if "entry" in c.kinds:
        return "abi" if c.register in uopt.ARG_REGS else "livein"
    if c.register in uopt.ARG_REGS and c.arg_use:
        return "abi"
    if c.register in uopt.RET_REGS and ("call" in c.kinds or c.return_use):
        return "abi"
    if "call" in c.kinds:
        return "abi" if c.use_lines else "dead"
    return "scored"


def as_webs(analysis, lines: list[str]) -> tuple[list[uopt.Web], dict, dict]:
    """uopt.Web per live component, with its class; weights use loop depth."""
    depth = uopt._loop_depth(lines)
    webs, classes, crossing = [], {}, {}
    roots = sorted(analysis["components"],
                   key=lambda r: min(analysis["components"][r].def_lines
                                     + analysis["components"][r].use_lines or [-1]))
    for number, root in enumerate(roots, start=1):
        c = analysis["components"][root]
        kind = classify(c)
        if kind in ("dead", "livein"):
            continue
        occ = sorted(c.def_lines + c.use_lines)
        w = uopt.Web(number=root, register=c.register)
        w.occurrences = len(occ)
        w.weight = sum(10.0 ** depth[i] for i in occ if 0 <= i < len(depth))
        w.lines = occ
        w.precolored = kind == "abi"
        w.chronology = number
        webs.append(w)
        classes[root] = kind
        crossing[root] = c.crosses_call
    return webs, classes, crossing


def replay(webs: list[uopt.Web], adjacency: dict, crossing: dict,
           order: list[uopt.Web]) -> dict[int, str]:
    """Greedy lowest-free colouring in `order`; ABI webs are fixed in place."""
    assigned = {w.number: w.register for w in webs if w.precolored}
    for w in order:
        if w.precolored:
            continue
        pool = CALLEE if crossing.get(w.number) else uopt.COLOR_POOL
        taken = {assigned[n] for n in adjacency.get(w.number, ()) if n in assigned}
        assigned[w.number] = next((r for r in pool if r not in taken), "UNCOLORABLE")
    return assigned


def _orders(webs: list[uopt.Web]) -> dict[str, list[uopt.Web]]:
    return {
        "model": sorted(webs, key=lambda w: (-w.save, w.chronology)),
        "oracle": sorted(webs, key=lambda w: (uopt.COLOR_INDEX[w.register], w.chronology)),
        "chrono": sorted(webs, key=lambda w: w.chronology),
    }


def evaluate(asm: str, seeds: int = 20, granularity: str = "instruction") -> dict | str | None:
    analysis = analyse(asm)
    if analysis is None or analysis == "unresolved":
        return analysis
    lines = [i.text for i in analysis["insns"]]
    webs, _classes, crossing = as_webs(analysis, lines)
    scored = [w for w in webs if not w.precolored]
    if len(scored) < 2:
        return None
    adjacency = analysis["block_adjacency" if granularity == "block" else "adjacency"]
    # Order-independent soundness check: with every neighbour in its ACTUAL
    # register, IDO's own choice must be free. Below 100% means false edges --
    # the refutation test for a denser interference hypothesis.
    actual = {w.number: w.register for w in webs}
    free_ok = lowest_free = 0
    for w in scored:
        taken = {actual[n] for n in adjacency.get(w.number, ()) if n in actual}
        pool = [r for r in (CALLEE if crossing.get(w.number) else uopt.COLOR_POOL) if r not in taken]
        free_ok += w.register in pool
        lowest_free += bool(pool) and pool[0] == w.register

    def hits(order):
        got = replay(webs, adjacency, crossing, order)
        return sum(got[w.number] == w.register for w in scored), got

    result = {"webs": len(scored), "actual_free": free_ok, "actual_lowest_free": lowest_free}
    for name, order in _orders(webs).items():
        result[name], got = hits(order)
        if name == "oracle":
            result["oracle_lower"] = sum(
                uopt.COLOR_INDEX.get(got[w.number], 99) < uopt.COLOR_INDEX[w.register]
                for w in scored if got[w.number] != w.register)
    rng = random.Random(20260912)
    total = 0
    for _ in range(seeds):
        shuffled = webs[:]
        rng.shuffle(shuffled)
        total += hits(shuffled)[0]
    result["random"] = total / seeds
    result["single_occurrence"] = sum(w.occurrences == 1 for w in scored)
    cross = [w for w in scored if crossing.get(w.number)]
    result["crossing"] = len(cross)
    result["crossing_in_callee"] = sum(w.register in CALLEE for w in cross)
    result["actual_registers"] = dict(Counter(w.register for w in scored))
    return result


def tier(instructions: int | None) -> str:
    n = instructions or 0
    return "<=30" if n <= 30 else "31-80" if n <= 80 else "81-200" if n <= 200 else "201+"


SUMMED = ("webs", "model", "oracle", "chrono", "random", "oracle_lower",
          "single_occurrence", "crossing", "crossing_in_callee",
          "actual_free", "actual_lowest_free")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    ap.add_argument("--status", required=True,
                    help="dashboard /api/map JSON: supplies status and size per function")
    ap.add_argument("--out", required=True)
    ap.add_argument("--granularity", choices=("instruction", "block"), default="instruction")
    args = ap.parse_args()

    status = {f["name"]: f for f in json.loads(Path(args.status).read_text(
        encoding="utf-8"))["functions"]}
    groups: dict[str, Counter] = defaultdict(Counter)
    skipped = Counter()
    per_function = []
    for dump in sorted(Path(args.repo, "nonmatchings").glob("*/target_object_dump_normalized.s")):
        meta = status.get(dump.parent.name)
        if not meta:
            skipped["no status"] += 1
            continue
        r = evaluate(dump.read_text(errors="replace"), granularity=args.granularity)
        if r == "unresolved":
            skipped["unresolved successor (jump table)"] += 1
            continue
        if not r:
            skipped["fewer than 2 scored webs"] += 1
            continue
        matched = meta["status"] in ("object_exact", "integrated")
        for key in ("all", f"{'matched' if matched else 'unmatched'}/{tier(meta.get('instructions'))}"):
            for k in SUMMED:
                groups[key][k] += r[k]
            groups[key]["functions"] += 1
        per_function.append({"function": dump.parent.name, "matched": matched,
                             "instructions": meta.get("instructions"), **r})

    rows = []
    print(f"{'group':20} {'fns':>5} {'webs':>6} {'model':>6} {'oracle':>6} {'chrono':>6} "
          f"{'random':>6}  1-occ  call->s  miss-lower  IDO-free  IDO-lowest")
    for key in sorted(groups, key=lambda k: (k != "all", k)):
        c = groups[key]
        pct = lambda k: round(100 * c[k] / c["webs"], 1) if c["webs"] else None
        misses = c["webs"] - c["oracle"]
        row = {"group": key, "functions": c["functions"], "webs": c["webs"],
               **{k: pct(k) for k in ("model", "oracle", "chrono", "random", "single_occurrence")},
               "crossing_in_callee_pct": round(100 * c["crossing_in_callee"] / c["crossing"], 1)
               if c["crossing"] else None,
               "oracle_miss_lower_pct": round(100 * c["oracle_lower"] / misses, 1) if misses else None,
               "actual_free_pct": pct("actual_free"), "actual_lowest_free_pct": pct("actual_lowest_free")}
        rows.append(row)
        print(f"{key:20} {row['functions']:5} {row['webs']:6} {row['model']:6} {row['oracle']:6} "
              f"{row['chrono']:6} {row['random']:6}  {row['single_occurrence']:5}  "
              f"{row['crossing_in_callee_pct']!s:>7}  {row['oracle_miss_lower_pct']!s:>9}  "
              f"{row['actual_free_pct']!s:>8}  {row['actual_lowest_free_pct']!s:>9}")
    print("skipped:", dict(skipped))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"granularity": args.granularity, "groups": rows, "skipped": dict(skipped),
                               "functions": per_function}, indent=2) + "\n", encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
