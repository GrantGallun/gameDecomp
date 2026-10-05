"""Was the no-op a stepping stone? Undo it inside the final match and recompile.

noop_stepping.py: 259 real no-op steps (C changed, same object) have an exact descendant, and in
208 the exact keeps every line the no-op changed. Kept is not needed: an inert change survives
because nothing touched it. Counterfactual: take the nearest exact E below the no-op N (parent P),
reverse the P->N edit inside E, recompile.
  E' still exact   -> the change is inert in E too: the no-op contributed nothing
  E' not exact     -> dormant when made, load-bearing by the match: a real stepping stone
E itself is recompiled first; a case counts only if E still matches today (environment drift).
Compiles run in isolated copies (native/repos/<fn>); nothing is logged to a KB.

    python3 noop_ablation.py [limit]   (cwd holding campaign.sqlite)
"""
import collections
import difflib
import hashlib
import json
import sqlite3
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import campaign_workers  # noqa: E402
from solver import workspace  # noqa: E402

REPO = Path("/home/grant/decomp/sbk1")
NATIVE = Path("/home/grant/decomp/experiments/noop-ablation-20260927")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)


def body(diff):
    lines = (diff or "").splitlines()
    while lines and lines[0].startswith(("--- ", "+++ ")):
        lines = lines[1:]
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()


att = {}
for i, addr, score, comp, ex, diff, sha in db.execute(
        "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0), diff_summary, "
        "source_sha256 from attempts"):
    att[i] = (addr, score or 0.0, comp, ex, body(diff), sha)
names = dict(db.execute("select addr, name from functions"))
kids = collections.defaultdict(list)
for p, c in db.execute("select parent_attempt_id, child_attempt_id from attempt_edges"):
    if p in att and c in att:
        kids[p].append(c)


def source(aid):
    return db.execute("select source_code from attempts where id=?", (aid,)).fetchone()[0] or ""


def nearest_exact(n):
    seen, queue = {n}, deque(kids.get(n, ()))
    while queue:
        k = queue.popleft()
        if k in seen:
            continue
        seen.add(k)
        if att[k][3]:
            return k
        queue.extend(kids.get(k, ()))
    return None


def reverse_edit(p_src, n_src, e_src):
    """Apply N->P inside E: every hunk N changed relative to P must appear verbatim in E."""
    p_lines, n_lines = p_src.splitlines(keepends=True), n_src.splitlines(keepends=True)
    out = e_src
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, p_lines, n_lines).get_opcodes():
        if tag == "equal":
            continue
        new, old = "".join(n_lines[j1:j2]), "".join(p_lines[i1:i2])
        if not new:
            return None                      # pure deletion: nothing in E to locate it by
        if out.count(new) != 1:
            return None                      # absent or ambiguous in E
        out = out.replace(new, old)
    return out if out != e_src else None


cases = []
for p, cs in kids.items():
    pa = att[p]
    if not pa[2] or pa[3]:
        continue
    for c in cs:
        ca = att[c]
        if not ca[2] or ca[3] or ca[4] != pa[4] or (ca[5] and ca[5] == pa[5]):
            continue                         # only REAL no-ops: same object, different source
        e = nearest_exact(c)
        if e is not None:
            cases.append((p, c, e))
print(f"real no-op steps with an exact descendant: {len(cases)}", flush=True)

result = collections.Counter()
examples = []
for p, c, e in cases[:LIMIT]:
    name = names.get(att[e][0])
    p_src, n_src, e_src = source(p), source(c), source(e)
    ablated = reverse_edit(p_src, n_src, e_src)
    if ablated is None:
        result["edit not locatable in the exact"] += 1
        continue
    iso = campaign_workers.isolate(REPO, NATIVE / "repos" / name, name)
    ws = iso / "nonmatchings" / name
    control = workspace.score(ws, iso, f"{name}_ctl", e_src)
    if not control.exact:
        result["exact no longer matches today (drift)"] += 1
        continue
    test = workspace.score(ws, iso, f"{name}_abl", ablated)
    if not test.compiled:
        verdict = "undo breaks compilation"
    elif test.exact:
        verdict = "inert: still exact without the no-op's change"
    else:
        verdict = "STEPPING STONE: exact breaks without the no-op's change"
    result[verdict] += 1
    if verdict.startswith("STEPPING") and len(examples) < 8:
        examples.append({"function": name, "parent": p, "noop": c, "exact": e,
                         "score_without": test.score,
                         "edit": "".join(difflib.unified_diff(p_src.splitlines(True),
                                                             n_src.splitlines(True), n=0))[:600]})
    print(json.dumps({"function": name, "verdict": verdict}), flush=True)
print(json.dumps(dict(result), indent=1))
for x in examples:
    print(json.dumps(x, indent=1))
