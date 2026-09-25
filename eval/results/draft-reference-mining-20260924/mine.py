"""Mine (residual feature -> C-shape edit family) associations from draft/reference pairs; measure population coverage.

Implements PROTOCOL.md exactly. No compiles. Reads E/rows (pairs.py) and the restart round-3 population rows.

    python3 mine.py            -> analysis.json (aggregates only; no reference source is written here)
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE.parent / "branch-layout-20260924"))
import census  # noqa: E402
from eval import mechanism_roadmap  # noqa: E402
from tools import n64_corpus  # noqa: E402

E = Path.home() / "decomp/experiments/draft-reference-mining-20260924"
POP = Path.home() / "decomp/experiments/restart-round3-20260923"
REPO = Path.home() / "decomp/sbk1"
RELOC = re.compile(r"%(hi|lo)\([^)]*\)")
MIN_N, MIN_K, MIN_P, MIN_LIFT = 10, 5, 0.30, 2.0


# ---------------------------------------------------------------- residual features
def mask(dump: str) -> list[str]:
    return [RELOC.sub(r"%\1(R)", line) for line in dump.splitlines() if line.strip()]


def gnu_diff(t: list[str], c: list[str]) -> str:
    with tempfile.TemporaryDirectory() as d:
        tp, cp = Path(d) / "t.s", Path(d) / "c.s"
        tp.write_text("\n".join(t) + "\n")
        cp.write_text("\n".join(c) + "\n")
        r = subprocess.run(["diff", "-u", "--suppress-common-lines", str(tp), str(cp)], capture_output=True, text=True)
    return r.stdout


def frame(ins) -> int | None:
    for op, args in ins[:4]:
        if op == "addiu" and args[:2] == ["sp", "sp"] and args[2].startswith("-"):
            return int(args[2][1:], 0)
    return None


def residual(target_dump: str, cand_dump: str) -> tuple[set[str], int] | None:
    t, c = mask(target_dump), mask(cand_dump)
    if t == c:
        return None
    ti, ci = census.parse(t), census.parse(c)
    cls, _ = census.classify(ti, ci)
    feats = {f"S:{cls}"}
    feats |= {f"R:{k}" for k, _stated, _loc in mechanism_roadmap.classes(gnu_diff(t, c), None)}
    ft, fc = frame(ti), frame(ci)
    if ft != fc:
        feats.add("F:bigger" if (fc or 0) > (ft or 0) else "F:smaller")
    return feats, len(ti)


# ---------------------------------------------------------------- C-shape features (shapes only, never names)
TYPE = (r"(?:const\s+|volatile\s+|unsigned\s+|signed\s+)*(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|char|short|"
        r"long|float|double|void|struct\s+\w+|union\s+\w+|[A-Z]\w*)")
NOT_DECL = {"return", "goto", "else", "case", "do", "break", "continue", "if", "while", "for", "switch", "sizeof"}
DECL = re.compile(r"^\s*(?:register\s+|static\s+|const\s+|volatile\s+)*(?:unsigned\s+|signed\s+)?"
                  r"(struct\s+\w+|union\s+\w+|enum\s+\w+|[A-Za-z_]\w*)(?:\s+|\s*\*+\s*)[\*\s]*[A-Za-z_]\w*\s*"
                  r"(?:\[[^\]]*\]\s*)*(?:=[^;]*)?;", re.M)
PATTERNS = {
    "if": r"\bif\b", "else": r"\belse\b", "?": r"\?", "return": r"\breturn\b", "for": r"\bfor\b",
    "do": r"\bdo\b", "goto": r"\bgoto\b", "switch": r"\bswitch\b", "case": r"\bcase\b", "break": r"\bbreak\b",
    "continue": r"\bcontinue\b", "register": r"\bregister\b", "volatile": r"\bvolatile\b",
    "cast": r"\(\s*" + TYPE + r"\s*\**\s*\)(?=\s*[\w(*&!~-])",
    "compound": r"(?<![=!<>])(?:<<|>>|[-+*/%&|^])=(?!=)", "incdec": r"\+\+|--", "stmt": r";", "subscript": r"\[",
    "arrow": r"->",
}
LABEL = re.compile(r"^\s*([A-Za-z_]\w*)\s*:(?!:)", re.M)


def shape(definition: str) -> collections.Counter:
    masked = n64_corpus._mask_noncode(definition)
    body = masked[masked.index("{"):] if "{" in masked else masked
    out = collections.Counter({k: len(re.findall(p, body)) for k, p in PATTERNS.items()})
    out["while"] = len(re.findall(r"\bwhile\b", body)) - out["do"]
    out["label"] = sum(m.group(1) not in ("default", "case") for m in LABEL.finditer(body))
    out["decl"] = sum(m.group(1) not in NOT_DECL for m in DECL.finditer(body))
    return out


def families(draft_def: str, ref_def: str) -> set[str]:
    a, b = shape(draft_def), shape(ref_def)
    return {f"{k}{'+' if b[k] > a[k] else '-'}" for k in set(a) | set(b) if a[k] != b[k]}


# ---------------------------------------------------------------- association
def associations(pairs: list[dict]) -> dict[tuple[str, str], dict]:
    n = len(pairs)
    n_r, n_e, k_re = collections.Counter(), collections.Counter(), collections.Counter()
    for p in pairs:
        n_r.update(p["features"])
        n_e.update(p["families"])
        for r in p["features"]:
            for e in p["families"]:
                k_re[(r, e)] += 1
    out = {}
    for (r, e), k in k_re.items():
        nr = n_r[r]
        p_r = k / nr
        rest = n - nr
        p_not = (n_e[e] - k) / rest if rest else 0.0
        lift = p_r / p_not if p_not else float("inf")
        out[(r, e)] = {"n_r": nr, "k": k, "p": p_r, "lift": lift,
                       "enriched": nr >= MIN_N and k >= MIN_K and p_r >= MIN_P and lift >= MIN_LIFT}
    return out, n_r


def enriched_by_feature(assoc) -> dict[str, list[tuple[str, dict]]]:
    out = collections.defaultdict(list)
    for (r, e), a in assoc.items():
        if a["enriched"]:
            out[r].append((e, a))
    for r in out:
        out[r].sort(key=lambda x: (-x[1]["lift"], -x[1]["k"]))
    return out


def compile_root(name: str) -> str | None:
    try:
        return json.loads((REPO / "nonmatchings" / name / ".compiler-target.json").read_text())["target"]
    except (OSError, ValueError, KeyError):
        return None


# ---------------------------------------------------------------- main
def size_bin(n: int) -> str:
    return "small" if n < 50 else "medium" if n < 150 else "large"


def mining_pairs() -> tuple[list[dict], dict]:
    stats = collections.Counter()
    by_size = collections.defaultdict(collections.Counter)
    pairs = []
    for path in sorted((E / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        stats["rows"] += 1
        ref, draft, target = row.get("ref") or {}, row.get("draft") or {}, row.get("target_dump")
        if not ref.get("compiled") or not target:
            stats["ref-not-compiled"] += 1
            continue
        size = size_bin(len(mask(target)))
        by_size[size]["rows"] += 1
        stats["ref-exact-raw"] += bool(ref.get("exact"))
        if mask(ref["dump"]) != mask(target):
            stats["ref-not-exact-mod-reloc"] += 1
            continue
        stats["usable"] += 1
        by_size[size]["usable"] += 1
        if not draft.get("compiled"):
            stats["draft-not-compiled" if row.get("status") == "ok" else "no-draft"] += 1
            continue
        by_size[size]["draft-compiled"] += 1
        res = residual(target, draft["dump"])
        if res is None:
            stats["draft-exact-mod-reloc"] += 1
            by_size[size]["draft-exact"] += 1
            continue
        stats["pairs"] += 1
        by_size[size]["pairs"] += 1
        pairs.append({"function": row["function"], "tu": compile_root(row["function"]), "features": res[0],
                      "families": families(row["draft_def"], row["ref_def"]), "size": size})
    return pairs, {"counts": dict(stats), "by_size": {k: dict(v) for k, v in by_size.items()}}


def population() -> tuple[list[dict], collections.Counter]:
    out, stats = [], collections.Counter()
    for path in sorted((POP / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        if row.get("exact"):
            stats["exact"] += 1
            continue
        if not row.get("world"):
            stats["no-world"] += 1
            continue
        name = row["function"]
        world = json.loads(Path(row["world"]).read_text())["world"]
        node = next(n for n in world["nodes"] if n["id"] == row["best_id"])
        v = node["verdict"]
        tpath = POP / "ws" / name / row["arm"] / "nonmatchings" / name / "target_object_dump_normalized.s"
        if not v.get("compiled") or not v.get("raw_diff") or not tpath.exists():
            stats["no-diff"] += 1
            continue
        tl = tpath.read_text().splitlines()
        try:
            cl = census.apply_diff(tl, v["raw_diff"])
        except (ValueError, IndexError):
            stats["diff-does-not-apply"] += 1
            continue
        res = residual("\n".join(tl), "\n".join(cl))
        if res is None:
            stats["exact-mod-reloc"] += 1
            continue
        stats["unsolved-with-residual"] += 1
        out.append({"function": name, "tu": compile_root(name), "features": res[0], "size": size_bin(res[1]),
                    "score": v.get("score")})
    return out, stats


def coverage(pop, assoc_fn):
    rows = []
    for u in pop:
        enr, n_r = assoc_fn(u)
        feats = sorted(u["features"])
        covered = [f for f in feats if enr.get(f)]
        unseen = [f for f in feats if n_r.get(f, 0) < MIN_N]
        rows.append({"function": u["function"], "size": u["size"], "features": feats, "covered": covered,
                     "unseen": unseen, "any": bool(covered), "all": len(covered) == len(feats)})
    n = len(rows)
    return rows, {"U": n, "covered_any": sum(r["any"] for r in rows), "covered_all": sum(r["all"] for r in rows),
                  "any_share": sum(r["any"] for r in rows) / n if n else 0,
                  "all_share": sum(r["all"] for r in rows) / n if n else 0}


def main() -> int:
    pairs, mining_stats = mining_pairs()
    assoc, n_r = associations(pairs)
    enr = enriched_by_feature(assoc)

    def control(feature, fams):
        if n_r.get(feature, 0) < MIN_N:
            return "untestable"
        hits = [e for e, _ in enr.get(feature, []) if e.rstrip("+-") in fams]
        return {"verdict": "fires" if hits else "FAILS", "families": hits}
    controls = {"C1 S:count": control("S:count", {"else", "?", "return"}),
                "C2 S:loop-shape": control("S:loop-shape", {"for", "while", "do", "goto", "label"})}

    pop, pop_stats = population()
    rows, cov = coverage(pop, lambda u: (enr, n_r))

    by_tu = collections.defaultdict(list)
    for p in pairs:
        by_tu[p["tu"]].append(p)
    cache = {}

    def tu_excluded(u):
        if u["tu"] not in cache:
            a, nr = associations([p for p in pairs if p["tu"] != u["tu"]])
            cache[u["tu"]] = (enriched_by_feature(a), nr)
        return cache[u["tu"]]
    _, cov_tu = coverage(pop, tu_excluded)

    suspect = any(isinstance(c, dict) and c["verdict"] == "FAILS" for c in controls.values())
    if suspect:
        verdict = "descriptive only (a positive control failed)"
    elif cov["all_share"] >= 0.40 and cov["any_share"] >= 0.70:
        verdict = "promising"
    elif cov["any_share"] < 0.30:
        verdict = "null"
    else:
        verdict = "mixed"

    demand_unseen = collections.Counter(f for r in rows for f in r["unseen"])
    demand_uncovered = collections.Counter(f for r in rows for f in r["features"] if f not in r["covered"])
    demand_covered = collections.Counter(f for r in rows for f in r["covered"])
    top = sorted(((r, e, a) for (r, e), a in assoc.items() if a["enriched"]),
                 key=lambda x: (-x[2]["lift"] * min(1, x[2]["k"] / 20), -x[2]["k"]))[:25]
    out = {
        "mining": mining_stats, "population": dict(pop_stats), "controls": controls,
        "coverage": cov, "coverage_tu_excluded": cov_tu, "verdict": verdict,
        "enriched_features": {r: [[e, a["k"], a["n_r"], round(a["p"], 3), round(a["lift"], 2)] for e, a in v[:6]]
                              for r, v in sorted(enr.items())},
        "top_associations": [[r, e, a["k"], a["n_r"], round(a["p"], 3), round(a["lift"], 2)] for r, e, a in top],
        "feature_support": dict(sorted(n_r.items(), key=lambda x: -x[1])),
        "population_demand": {"covered": demand_covered.most_common(), "uncovered": demand_uncovered.most_common(),
                              "unseen": demand_unseen.most_common()},
        "population_rows": rows,
    }
    (HERE / "analysis.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("mining", "population", "controls", "coverage", "coverage_tu_excluded",
                                          "verdict")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
