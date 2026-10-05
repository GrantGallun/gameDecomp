"""Does variable coalescing reach matches register search never generates? Deterministic, no reference.

Diagnosis (diagnose.py, dev-only): on stepRaceMotionLoopingAnimation every arm spent 54-107 compiles and never
proposed the one edit that matches: m2c gives each loaded value its own temporary (`temp_v0`, `temp_h0`)
where the original reuses one variable. `local_web_merge` only merges pointer locals in opposite if/else arms.

Prototype generator: for two scalar locals declared in the function body, when every textual use of the
second comes after the last use of the first, rename the second to the first and drop its declaration.
Textual order is a heuristic for non-overlapping live ranges; the compiler is the judge (a wrong merge just
fails to match). Run on the ROOT source of every function in both mutation-selection trials.

    python3 coalesce_probe.py      (WSL)
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from patterns.commit_provenance import function_definitions  # noqa: E402
from solver import workspace  # noqa: E402

SCALAR = r"(?:s8|u8|s16|u16|s32|u32|s64|u64|f32|f64|int|short|char|unsigned\s+\w+|signed\s+\w+)"
DECL = re.compile(rf"^[ \t]*(?P<type>{SCALAR})[ \t]+(?P<name>[A-Za-z_]\w*)[ \t]*;[ \t]*\n", re.M)
FLOATS = {"f32", "f64"}


def variants(source: str, function: str, limit: int = 12):
    body = function_definitions(source).get(function)
    if not body:
        return []
    start = source.index(body)
    decls = [(m.group("type"), m.group("name"), m) for m in DECL.finditer(body)]
    uses = {}
    for _t, name, m in decls:
        spans = [u.start() for u in re.finditer(rf"\b{re.escape(name)}\b", body) if u.start() != m.start("name")]
        if spans:
            uses[name] = spans
    out = []
    for ta, a, _ma in decls:
        for tb, b, mb in decls:
            if a == b or a not in uses or b not in uses or ((ta in FLOATS) != (tb in FLOATS)):
                continue
            if min(uses[b]) <= max(uses[a]):
                continue                          # b is used before a's last use: may overlap
            new_body = body[:mb.start()] + body[mb.end():]
            new_body = re.sub(rf"\b{re.escape(b)}\b", a, new_body)
            out.append((f"coalesce:{b}->{a}", source[:start] + new_body + source[start + len(body):]))
            if len(out) >= limit:
                return out
    return out


roots = {}
for exp in ("evolvability-trial-20260928", "evolvability-replication-20260928"):
    for p in sorted(Path(f"/home/grant/decomp/experiments/{exp}").glob("run-*/**/production/result.json")):
        r = json.loads(p.read_text())
        roots.setdefault(r["function"], (r["events"][0]["source"], tuple(r["baseline_gradient"] or ())))

tally = collections.Counter()
for name, (root, gradient) in sorted(roots.items()):
    cands = variants(root, name)
    tally["functions"] += 1
    tally["with a candidate"] += bool(cands)
    if not cands:
        continue
    iso = campaign_workers.isolate(Path("/home/grant/decomp/sbk1"),
                                   Path("/home/grant/decomp/experiments/coalesce-probe-20260928") / name, name)
    ws = iso / "nonmatchings" / name
    base = workspace.score(ws, iso, f"{name}_cbase", root)
    best, hit = base.score if base.compiled else 0, None
    for i, (label, code) in enumerate(cands):
        att = workspace.score(ws, iso, f"{name}_c{i}", code)
        tally["compiles"] += 1
        if att.compiled and att.exact:
            hit = label
            break
        if att.compiled and att.score > best:
            best = att.score
    kind = "register-only" if gradient and gradient[0] == 0 else "structural"
    tally[f"{kind} candidates"] += 1
    if hit:
        tally[f"{kind} EXACT"] += 1
    elif best > (base.score if base.compiled else 0):
        tally[f"{kind} score up"] += 1
    print(json.dumps({"function": name, "residual": kind, "candidates": len(cands), "exact": hit,
                      "base": base.score, "best": best}), flush=True)
print(json.dumps(tally, indent=1))
