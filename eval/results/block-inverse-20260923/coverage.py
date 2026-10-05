"""Block-inverse feasibility (PROTOCOL.md): tile target blocks with (C line -> instruction shape) pairs from other functions."""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver.source_attribution import instructions_of  # noqa: E402

HERE = Path(__file__).resolve().parent
EXP = Path.home() / "decomp/experiments"
RUNS = ["population-transfer-20260922/rows", "population-transfer-20260922/stage2/rows", "gated-population-20260923/rows",
        "index-form-population-20260923/rows", "locality-population-20260923/rows", "narrow-population-20260923/rows"]
REPO = Path.home() / "decomp/sbk1"
REGCLASS = [(re.compile(r"^\$?(t[0-9]|at)$"), "T"), (re.compile(r"^\$?s[0-8]$"), "S"), (re.compile(r"^\$?a[0-3]$"), "A"),
            (re.compile(r"^\$?v[01]$"), "V"), (re.compile(r"^\$?f\d+$"), "F"), (re.compile(r"^\$?(sp|ra|zero|fp|gp)$"), None)]
BRANCH = re.compile(r"^(b\w*|j|jal|jr|jalr)$")


def operand(o: str, loose: bool) -> str:
    o = o.strip()
    for pat, cls in REGCLASS:
        if pat.match(o):
            return cls or o.lstrip("$")
    m = re.match(r"^(.*)\((\$?\w+)\)$", o)
    if m:
        return f"{operand(m.group(1), loose)}({operand(m.group(2), loose)})"
    if "%" in o:
        return re.sub(r"\(([A-Za-z_]\w*)", "(SYM", re.sub(r"\+\s*\w+", "", o))
    return "K" if loose else o


def shape(insn: str, loose: bool) -> str:
    parts = insn.replace("\t", " ").split(None, 1)
    if not parts:
        return ""
    op = parts[0]
    if BRANCH.match(op):
        return op                                       # targets are positions, not facts
    ops = [operand(x, loose) for x in parts[1].split(",")] if len(parts) > 1 else []
    return op + " " + ",".join(ops)


def pairs():
    """function -> list of shape tuples, one per (node, C line) group of consecutive instructions."""
    by_fn = collections.defaultdict(set)
    for run in RUNS:
        for path in sorted((EXP / run).glob("*.json")):
            row = json.loads(path.read_text())
            if not row.get("world") or not Path(row["world"]).exists():
                continue
            for n in json.loads(Path(row["world"]).read_text())["world"]["nodes"]:
                att = n["verdict"].get("source_attribution") or {}
                if not n["verdict"]["compiled"] or att.get("status") != "verified":
                    continue
                group, line = [], None
                for r in instructions_of(att):
                    cl = r.get("candidate_line")
                    text = r.get("instruction") or ""
                    if cl != line and group:
                        by_fn[row["function"]].add(tuple(group))
                        group = []
                    line = cl
                    if cl is not None and text:
                        group.append(text)
                if group:
                    by_fn[row["function"]].add(tuple(group))
    return by_fn


def blocks(dump: str) -> list[list[str]]:
    out, cur = [], []
    lines = [l.strip() for l in dump.splitlines() if l.strip() and not l.strip().endswith(":")]
    i = 0
    while i < len(lines):
        cur.append(lines[i])
        op = lines[i].split()[0]
        if BRANCH.match(op) and op != "jal":
            if i + 1 < len(lines):
                cur.append(lines[i + 1])                # delay slot
                i += 1
            out.append(cur)
            cur = []
        i += 1
    if cur:
        out.append(cur)
    return out


def tiles(seq: list[str], vocab: set[tuple], maxlen: int) -> bool:
    ok = [False] * (len(seq) + 1)
    ok[0] = True
    for i in range(len(seq)):
        if not ok[i]:
            continue
        for L in range(1, min(maxlen, len(seq) - i) + 1):
            if tuple(seq[i:i + L]) in vocab:
                ok[i + L] = True
    return ok[len(seq)]


def main():
    by_fn = pairs()
    unsolved = {json.loads(p.read_text())["function"] for p in (EXP / "locality-population-20260923/rows").glob("*.json")
                if not json.loads(p.read_text()).get("exact")}
    result = {}
    for loose in (False, True):
        vocab_by_fn = {f: {tuple(shape(x, loose) for x in g) for g in groups} for f, groups in by_fn.items()}
        tot_ins = cov_ins = tot_blk = cov_blk = 0
        per_fn = []
        for f in sorted(unsolved):
            dump = REPO / "nonmatchings" / f / "target_object_dump_normalized.s"
            if not dump.exists():
                continue
            vocab = set().union(*(v for g, v in vocab_by_fn.items() if g != f))
            maxlen = max((len(s) for s in vocab), default=1)
            fb = fc = 0
            for blk in blocks(dump.read_text(errors="replace")):
                seq = [shape(x, loose) for x in blk]
                good = tiles(seq, vocab, maxlen)
                tot_blk += 1
                cov_blk += good
                tot_ins += len(seq)
                cov_ins += len(seq) if good else 0
                fb += 1
                fc += good
            per_fn.append(fc / fb if fb else 0)
        per_fn.sort()
        result["loose" if loose else "strict"] = {
            "blocks": tot_blk, "blocks_fully_tiled": round(cov_blk / max(1, tot_blk), 4),
            "instructions_in_tiled_blocks": round(cov_ins / max(1, tot_ins), 4),
            "functions_fully_tiled": sum(1 for x in per_fn if x == 1.0), "functions": len(per_fn),
            "median_function_block_share": round(per_fn[len(per_fn) // 2], 3) if per_fn else None}
    result["pairs_observed"] = sum(len(g) for g in by_fn.values())
    result["functions_contributing"] = len(by_fn)
    (HERE / "coverage.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
