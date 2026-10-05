"""Does WHERE an edit lands predict whether it changes the bytes? Read-only, campaign edges.

solver.residual_sites maps each mismatched instruction to the C line IDO attributes it to (verified
object line records; `direct-compiler-line`). It feeds the tree prompt and ranks edits in the
differential-repair pilot, but its predictive value was never measured. Here every logged edit
(parent -> child, both compiled, source changed) is classed by what it touched in the PARENT:

  owner      a line that directly owns a mismatched instruction
  live       a line that emits instructions, none of them mismatched
  silent     only lines that emit no instruction (declarations, blank lines, the signature...)

and scored on outcomes: object changed (not a no-op), score up, reached an exact.

    python3 edit_locality.py [sample]   (cwd holding campaign.sqlite)
"""
import collections
import hashlib
import json
import random
import sqlite3
import re
import sys

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import code_shapes, residual_sites  # noqa: E402
from solver.source_attribution import instructions_of  # noqa: E402

SAMPLE = int(sys.argv[1]) if len(sys.argv) > 1 else 40000
db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)
names = dict(db.execute("select addr, name from functions"))


def body(diff):
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()


REG = re.compile(r"\$?\b(?:v[01]|a[0-3]|t[0-9]|s[0-8]|f[0-9]+|at|ra|fp|k[01])\b")


def regnorm(diff):
    """The diff body with register names erased and hunk positions dropped: equal for two
    listings that differ ONLY in which registers were allocated."""
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return hashlib.sha1("\n".join(REG.sub("R", l) for l in lines
                                  if not l.startswith("@@")).encode()).hexdigest()


strategy = dict(db.execute("select id, coalesce(strategy,'') from attempts"))
meta = {}
for i, addr, score, comp, ex, sha, diff in db.execute(
        "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0), source_sha256, "
        "diff_summary from attempts"):
    meta[i] = (addr, score or 0.0, comp, ex, sha, body(diff), regnorm(diff))
edges = [(p, c) for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges")
         if p in meta and c in meta and meta[p][2] and not meta[p][3] and meta[c][2]
         and meta[p][4] != meta[c][4]]
random.Random(20260927).shuffle(edges)
edges = edges[:SAMPLE]
print(f"edges sampled: {len(edges)}", flush=True)


def full(aid):
    src, diff, sampling = db.execute(
        "select source_code, diff_summary, sampling from attempts where id=?", (aid,)).fetchone()
    try:
        attribution = (json.loads(sampling or "{}") or {}).get("source_attribution")
    except ValueError:
        attribution = None
    return src or "", diff or "", attribution


cache = {}


def parent_view(p):
    """(mismatch-owner sites, live line spans) of a parent, or None if attribution is stale."""
    if p in cache:
        return cache[p]
    src, diff, attribution = full(p)
    name = names.get(meta[p][0])
    view = None
    try:
        mapping = residual_sites.source_map(src, name, diff, attribution)
    except Exception:
        mapping = None
    if mapping and mapping["direct_attribution_status"] == "verified":
        owners = [s for s in mapping["sites"] if s["evidence"] == "direct-compiler-line"]
        offsets = [0]
        for line in src.splitlines(keepends=True):
            offsets.append(offsets[-1] + len(line))
        live_lines = {r.get("candidate_line") for r in instructions_of(attribution)}
        live = [{"start": offsets[n - 1], "stop": offsets[n]} for n in live_lines
                if isinstance(n, int) and 1 <= n < len(offsets)]
        missing = sum(1 for g in mapping["gaps"] if g["reason"].startswith("target-only"))
        span = code_shapes._body(src, name)
        view = (owners, live, src, missing, (span[1], span[2]) if span else None)
    cache[p] = view
    return view


exact_memo = {}
kids = collections.defaultdict(list)
for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    kids[p].append(c)


def reaches_exact(n):
    if n in exact_memo:
        return exact_memo[n]
    stack, seen, found = [n], set(), False
    while stack and not found:
        k = stack.pop()
        if k in seen or k not in meta:
            continue
        seen.add(k)
        found = bool(meta[k][3])
        stack.extend(kids.get(k, ()))
    exact_memo[n] = found
    return found


table = collections.defaultdict(collections.Counter)
extra = collections.defaultdict(collections.Counter)
fams = collections.defaultdict(collections.Counter)
for p, c in edges:
    view = parent_view(p)
    if view is None:
        table["(stale attribution)"]["n"] += 1
        continue
    owners, live, p_src, missing, span = view
    c_src = db.execute("select source_code from attempts where id=?", (c,)).fetchone()[0] or ""
    region = residual_sites.edit_region(p_src, c_src)
    if any(residual_sites._overlap(region, s) for s in owners):
        cls = "owner"
    elif any(residual_sites._overlap(region, s) for s in live):
        cls = "live"
    else:
        cls = "silent"
    t = table[cls]
    t["n"] += 1
    t["object changed"] += meta[c][5] != meta[p][5]
    t["score up"] += meta[c][1] > meta[p][1]
    t["score down"] += meta[c][1] < meta[p][1]
    t["reached exact"] += reaches_exact(c)
    changed = meta[c][5] != meta[p][5]
    up = meta[c][1] > meta[p][1]
    # claim 2: "already correct" lines while the parent is MISSING instructions no line can own
    if cls == "live":
        sub = extra["live, parent missing instructions" if missing else "live, nothing missing"]
        sub["n"] += 1; sub["up"] += up; sub["down"] += meta[c][1] < meta[p][1]
    # claim 3: where silent edits land, and whether their byte change is register-only
    if cls == "silent":
        inside = span is not None and region["start"] < span[1] and region["stop"] > span[0]
        sub = extra["silent inside the function" if inside else "silent outside (context/helpers)"]
        sub["n"] += 1; sub["changed"] += changed; sub["up"] += up
        sub["register-only change"] += changed and meta[c][6] == meta[p][6]
    # claim 4: the owner/live comparison within strategy families
    fam = strategy.get(c, "").split(":")[0]
    fams[fam][cls, "n"] += 1
    fams[fam][cls, "up"] += up
print(f"{'edit touched':22s} {'n':>7s} {'obj changed':>12s} {'score up':>9s} {'score down':>11s} {'reached exact':>14s}")
for cls in ("owner", "live", "silent"):
    t = table[cls]
    n = max(t["n"], 1)
    print(f"{cls:22s} {t['n']:7d} {t['object changed'] / n:12.1%} {t['score up'] / n:9.1%}"
          f" {t['score down'] / n:11.1%} {t['reached exact'] / n:14.2%}")
print("stale attribution:", table["(stale attribution)"]["n"])

print()
print("claim 2 -- live-line edits split by whether the parent was missing instructions:")
for k, v in sorted(extra.items()):
    if k.startswith("live"):
        print(f"  {k:40s} n {v['n']:6d}  score up {v['up'] / max(v['n'], 1):6.1%}  down {v['down'] / max(v['n'], 1):6.1%}")
print("claim 3 -- silent edits:")
for k, v in sorted(extra.items()):
    if k.startswith("silent"):
        print(f"  {k:40s} n {v['n']:6d}  changed bytes {v['changed'] / max(v['n'], 1):6.1%}"
              f"  of those register-only {v['register-only change'] / max(v['changed'], 1):6.1%}"
              f"  score up {v['up'] / max(v['n'], 1):6.1%}")
print("claim 4 -- score-up rate by class within each strategy family (n):")
for fam, c in sorted(fams.items(), key=lambda kv: -sum(v for (k, m), v in kv[1].items() if m == "n"))[:10]:
    cells = "  ".join(f"{cls} {c[cls, 'up'] / max(c[cls, 'n'], 1):5.1%} ({c[cls, 'n']})"
                      for cls in ("owner", "live", "silent"))
    print(f"  {fam:36s} {cells}")
