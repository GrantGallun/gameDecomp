"""Localized width mining: width residual -> the local-type change the reference made on that line. See PROTOCOL.md.

    python3 linemine.py capture [--jobs 4]   recompile ref.c / draft.c with line capture (mining workspaces only)
    python3 linemine.py analyze              -> analysis.json (aggregates only; no reference source is written)
"""
from __future__ import annotations

import collections
import concurrent.futures
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MINING = HERE.parent / "draft-reference-mining-20260924"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(MINING))
import mine  # noqa: E402
from solver import source_attribution  # noqa: E402
from tools import n64_corpus  # noqa: E402

E = Path.home() / "decomp/experiments/width-mining-20260924"
WS = mine.E / "repo/nonmatchings"
WIDTH = {"sll", "sra", "srl", "andi", "lb", "lbu", "lh", "lhu", "sb", "sh"}
MIN_N, MIN_SHARE = 8, 0.60
PRIM = {"s8": "s8", "u8": "u8", "s16": "s16", "u16": "u16", "s32": "s32", "u32": "u32", "int": "s32",
        "signed int": "s32", "unsigned int": "u32", "unsigned": "u32", "signed": "s32", "short": "s16",
        "signed short": "s16", "short int": "s16", "unsigned short": "u16", "char": "u8", "unsigned char": "u8",
        "signed char": "s8", "long": "s32", "unsigned long": "u32"}
TYPE_RE = (r"(?:unsigned\s+(?:char|short|int|long)|signed\s+(?:char|short|int|long)|short\s+int|unsigned|signed|"
           r"s8|u8|s16|u16|s32|u32|int|char|short|long)")
LOCAL = re.compile(r"^[ \t]*(?:register[ \t]+|volatile[ \t]+|const[ \t]+|static[ \t]+)*(" + TYPE_RE + r")[ \t]+"
                   r"([^;(){}]+);", re.M)
PARAM = re.compile(r"(?:^|,)\s*(?:register\s+|const\s+|volatile\s+)*(" + TYPE_RE + r")\s+([A-Za-z_]\w*)\s*(?=,|$)")
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
LHS = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:<<|>>|[-+*/%&|^])?=(?!=)")


# ---------------------------------------------------------------- capture
def usable_rows():
    for path in sorted((mine.E / "rows").glob("*.json")):
        row = json.loads(path.read_text())
        ref, draft, target = row.get("ref") or {}, row.get("draft") or {}, row.get("target_dump")
        if not (ref.get("compiled") and draft.get("compiled") and target):
            continue
        if mine.mask(ref["dump"]) != mine.mask(target) or mine.mask(draft["dump"]) == mine.mask(target):
            continue
        yield row


def capture_one(name: str) -> dict:
    ws = WS / name
    recipes = sorted(ws.glob(".compiler-*.sh"))
    if len(recipes) != 1:
        return {"function": name, "status": "no single recipe"}
    out = {"function": name, "status": "ok"}
    for stem in ("ref", "draft"):
        script = source_attribution.prepare(ws, stem, recipes[0])
        if script == recipes[0]:
            return {"function": name, "status": "recipe has no strip line to hook"}
        r = subprocess.run(["bash", script.name, f"{stem}.c"], cwd=ws, capture_output=True, text=True, timeout=300)
        dump = ws / f"{stem}.source-lines.dump"
        norm = ws / f"{stem}_object_dump_normalized.s"
        if r.returncode != 0 or not dump.exists() or not norm.exists():
            return {"function": name, "status": f"{stem} capture failed"}
        records, _raw = source_attribution.parse_dump(dump.read_text())
        text = [rec for rec in records if rec["section"] == ".text"]
        normalized = [l for l in norm.read_text().splitlines()]
        if len(text) < len(normalized):
            return {"function": name, "status": f"{stem} fewer records than instructions"}
        out[stem] = {"lines": [rec["line"] for rec in text[:len(normalized)]], "normalized": normalized}
    return out


def capture(jobs: int) -> int:
    (E / "lines").mkdir(parents=True, exist_ok=True)
    names = [r["function"] for r in usable_rows() if not (E / "lines" / f"{r['function']}.json").exists()]
    print(len(names), "to capture", flush=True)
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        for i, res in enumerate(pool.map(capture_one, names), 1):
            (E / "lines" / f"{res['function']}.json").write_text(json.dumps(res))
            if i % 50 == 0 or res["status"] != "ok":
                print(i, res["function"], res["status"], flush=True)
    return 0


# ---------------------------------------------------------------- variables
def variables(file_text: str, name: str) -> tuple[dict[str, tuple[str, str]], int, int]:
    """(name -> (normalized primitive type, 'local'|'param'), first line, last line) of `name`'s definition."""
    rec = next(r for r in n64_corpus.extract_functions(file_text) if r["name"] == name)
    definition = str(rec["definition"])
    start = file_text.find(definition)
    first = file_text.count("\n", 0, start) + 1
    last = first + definition.count("\n")
    masked = n64_corpus._mask_noncode(definition)
    brace = masked.index("{")
    header, body = masked[:brace], masked[brace:]
    out: dict[str, tuple[str, str]] = {}
    params = header[header.rfind("(", 0, header.rfind(")")) + 1:header.rfind(")")] if ")" in header else ""
    for m in PARAM.finditer(params):
        out[m.group(2)] = (PRIM[re.sub(r"\s+", " ", m.group(1))], "param")
    for m in LOCAL.finditer(body):
        t = PRIM[re.sub(r"\s+", " ", m.group(1))]
        for part in m.group(2).split(","):
            decl = part.split("=")[0].strip()
            if decl.startswith("*") or "[" in decl:
                continue
            if re.fullmatch(r"[A-Za-z_]\w*", decl):
                out[decl] = (t, "local")
    return out, first, last


def line_vars(text: str, decls: dict) -> tuple[list[str], str | None]:
    code = n64_corpus._mask_noncode(text)
    seen = []
    for ident in IDENT.findall(code):
        if ident in decls and ident not in seen:
            seen.append(ident)
    lhs = LHS.match(code)
    return seen, (lhs.group(1) if lhs and lhs.group(1) in decls else None)


# ---------------------------------------------------------------- analysis
def op(line: str) -> str:
    return line.split()[0] if line.split() else ""


def events_for(row: dict, cap: dict, stats: collections.Counter) -> list[dict]:
    name = row["function"]
    ws = WS / name
    t, d = mine.mask(row["target_dump"]), mine.mask(row["draft"]["dump"])
    if mine.mask("\n".join(cap["draft"]["normalized"])) != d or mine.mask("\n".join(cap["ref"]["normalized"])) != t:
        stats["recompile differs from pairs.py dump"] += 1
        return []
    tl, dl = cap["ref"]["lines"], cap["draft"]["lines"]
    ref_text, draft_text = (ws / "ref.c").read_text(), (ws / "draft.c").read_text()
    try:
        rv, rf, rl = variables(ref_text, name)
        dv, df, dlast = variables(draft_text, name)
    except (StopIteration, ValueError):
        stats["definition not found"] += 1
        return []
    rlines, dlines = ref_text.splitlines(), draft_text.splitlines()
    out = []

    def at(lines, i):
        while i >= 0 and lines[i] is None:
            i -= 1
        return lines[i] if i >= 0 else None

    sm = difflib.SequenceMatcher(None, t, d, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        steps = []
        if tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                ti, dj = (i1 + k if i1 + k < i2 else None), (j1 + k if j1 + k < j2 else None)
                steps.append((ti, dj))
        elif tag == "delete":
            steps = [(ti, None) for ti in range(i1, i2)]
        else:
            steps = [(None, dj) for dj in range(j1, j2)]
        for ti, dj in steps:
            to, do = (op(t[ti]) if ti is not None else None), (op(d[dj]) if dj is not None else None)
            if not ({to, do} & WIDTH):
                continue
            if ti is not None and dj is not None:
                kind = f"field:{to}" if to == do else f"opcode:{to}/{do}"
            elif ti is not None:
                kind = f"missing:{to}"
            else:
                kind = f"extra:{do}"
            rline = at(tl, ti if ti is not None else i1 - 1)
            dline = at(dl, dj if dj is not None else j1 - 1)
            stats["events"] += 1
            if not (rline and dline and rf <= rline <= rl and df <= dline <= dlast):
                stats["event outside the function's lines"] += 1
                continue
            rvars, rlhs = line_vars(rlines[rline - 1], rv)
            dvars, dlhs = line_vars(dlines[dline - 1], dv)
            if len(rvars) == 1 and len(dvars) == 1:
                pr, pd = rvars[0], dvars[0]
            elif rlhs and dlhs:
                pr, pd = rlhs, dlhs
            else:
                stats["unpaired"] += 1
                continue
            stats["paired"] += 1
            out.append({"kind": kind, "draft": dv[pd][0], "ref": rv[pr][0], "role": dv[pd][1]})
    return out


def clone_key(row):
    return (len(mine.mask(row["target_dump"])), tuple(sorted(mine.shape(row["draft_def"]).items())),
            tuple(sorted(mine.shape(row["ref_def"]).items())))


def analyze() -> int:
    stats = collections.Counter()
    cells = collections.defaultdict(collections.Counter)   # (kind, draft type) -> ref type -> dedup count
    per_kind = collections.defaultdict(lambda: [0, 0])      # kind -> [paired dedup, type changed dedup]
    seen = set()
    for row in usable_rows():
        path = E / "lines" / f"{row['function']}.json"
        if not path.exists():
            stats["no capture"] += 1
            continue
        cap = json.loads(path.read_text())
        if cap["status"] != "ok":
            stats[f"capture: {cap['status']}"] += 1
            continue
        stats["pairs analyzed"] += 1
        key = clone_key(row)
        for ev in events_for(row, cap, stats):
            k = (key, ev["kind"], ev["draft"], ev["ref"])
            if k in seen:
                continue
            seen.add(k)
            cells[(ev["kind"], ev["draft"])][ev["ref"]] += 1
            per_kind[ev["kind"]][0] += 1
            per_kind[ev["kind"]][1] += ev["draft"] != ev["ref"]
    rules = []
    for (kind, dt), refs in cells.items():
        n = sum(refs.values())
        rt, c = refs.most_common(1)[0]
        if n >= MIN_N and c / n >= MIN_SHARE:
            rules.append({"kind": kind, "draft": dt, "ref": rt, "n": n, "share": round(c / n, 3),
                          "changes_type": rt != dt})

    def ctl(kind, dts, ok):
        cs = [(dt, cells.get((kind, dt))) for dt in dts]
        cs = [(dt, c) for dt, c in cs if c and sum(c.values()) >= MIN_N]
        if not cs:
            return "untestable"
        hit = [r for r in rules if r["kind"] == kind and r["draft"] in dts and ok(r["ref"], r["draft"])]
        return {"verdict": "fires" if hit else "FAILS", "rules": hit}
    control = {"extra:andi (u8/u16 -> wider or signed)": ctl("extra:andi", {"u8", "u16"},
                                                           lambda r, d: r not in ("u8", "u16")),
               "missing:andi (s32 -> u8/u16)": ctl("missing:andi", {"s32"}, lambda r, d: r in ("u8", "u16"))}
    out = {"stats": dict(stats), "control": control,
           "rules": sorted(rules, key=lambda r: (-r["changes_type"], -r["n"])),
           "type_change_rate": {k: {"paired": v[0], "changed": v[1], "rate": round(v[1] / v[0], 3) if v[0] else None}
                                for k, v in sorted(per_kind.items(), key=lambda kv: -kv[1][0])},
           "cells": {f"{k}|{dt}": dict(c.most_common()) for (k, dt), c in
                     sorted(cells.items(), key=lambda kv: -sum(kv[1].values()))}}
    (HERE / "analysis.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("stats", "control", "rules")}, indent=1))
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["capture"]:
        sys.exit(capture(int(sys.argv[3]) if len(sys.argv) > 3 else 4))
    sys.exit(analyze())
