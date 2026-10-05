"""Where has the model contributed something deterministic search did not? Read-only.

Run under WSL from the campaign directory:
    python3 llm_census.py [campaign.sqlite]
"""
import collections
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
FREE = ("", "zero-model", "deterministic", "none", "m2c")


def is_model(model):
    return (model or "") not in FREE


# AUTHORSHIP, not labelling: ~55k rows carry model='gpt-oss:20b' because their run was configured
# with a model, but they are re-verifications (agentrepair-*-reverify) with no model output. The
# first version of this census counted them and reported 344 functions "reachable only through a
# model step" -- every one of those steps was a flat reverify. A model-authored attempt is one with
# a stored raw response.
AUTHORED = {row[0] for row in db.execute(
    "select id from attempts where length(coalesce(raw_response,'')) > 0 "
    "and coalesce(model,'') not in ('','zero-model','deterministic','none','m2c')")}


def fam(strategy):
    return (strategy or "").split(":")[0].split("@")[0]


att = {}
for row in db.execute("select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0), "
                      "coalesce(model,''), strategy, created_at from attempts"):
    att[row[0]] = row[1:]
name = dict(db.execute("select addr, name from functions"))

# --- 1. volume and per-attempt yield, model vs model-free --------------------------------------
vol = collections.defaultdict(lambda: collections.Counter())
for fid, (addr, score, comp, exact, model, strat, created) in att.items():
    if is_model(model) and fid not in AUTHORED:
        vol["model-labelled, not authored"]["attempts"] += 1
        continue
    side = "model" if fid in AUTHORED else "model-free"
    vol[side]["attempts"] += 1
    vol[side]["compiled"] += comp
    vol[side]["exact"] += exact
kids = collections.defaultdict(list)
edges = db.execute("select parent_attempt_id, child_attempt_id, relation from attempt_edges").fetchall()
for p, c, rel in edges:
    kids[p].append(c)
for p, c, rel in edges:
    if p not in att or c not in att:
        continue
    pa, ca = att[p], att[c]
    if is_model(ca[4]) and c not in AUTHORED:
        continue
    side = "model" if c in AUTHORED else "model-free"
    if pa[2] and ca[2] and ca[1] > pa[1]:
        vol[side]["improving_edges"] += 1
print("== volume ==")
for side, counts in vol.items():
    a = counts["attempts"]
    if side.startswith("model-labelled"):
        print(f"{side}: {a} attempts (excluded below)")
        continue
    print(f"{side:10s} attempts {a:7d}  compiled {counts['compiled']/a:6.1%}  "
          f"improving edges {counts['improving_edges']:6d} ({counts['improving_edges']/a:.2%})  "
          f"exact {counts['exact']:5d} ({counts['exact']/a*1000:.2f}/1k)")

# --- 2. exact functions: who got there, who got there first ------------------------------------
first = {}
for fid, (addr, score, comp, exact, model, strat, created) in sorted(att.items(),
                                                                       key=lambda kv: kv[1][6] or 0):
    if exact and not (is_model(model) and fid not in AUTHORED):
        first.setdefault(addr, {}).setdefault("model" if fid in AUTHORED else "model-free",
                                              (created, fam(strat)))
only_model = [a for a, d in first.items() if "model" in d and "model-free" not in d]
model_first = [a for a, d in first.items() if "model" in d and "model-free" in d
               and d["model"][0] < d["model-free"][0]]
print("\n== exact functions ==")
print("total", len(first), "| model-free only", sum(1 for d in first.values() if "model" not in d),
      "| model only", len(only_model), "| both, model first", len(model_first))
print("model-only exacts by strategy:",
      collections.Counter(first[a]["model"][1] for a in only_model).most_common(10))


# --- 3. does an exact DESCEND from a model step? (a model edit enabling a deterministic finish) --
parents = collections.defaultdict(list)
for p, c, rel in edges:
    parents[c].append(p)
memo = {}


def model_ancestor(node):
    if node in memo:
        return memo[node]
    memo[node] = False
    stack, seen = [node], set()
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n != node and n in AUTHORED:
            memo[node] = True
            return True
        stack.extend(parents.get(n, ()))
    return False


exact_ids = [i for i, a in att.items() if a[3]]
lineage = [i for i in exact_ids if i in parents]
via_model = [i for i in lineage if model_ancestor(i) and i not in AUTHORED]
fn_via_model = {att[i][0] for i in via_model}
fn_model_free_clean = {att[i][0] for i in lineage if i not in AUTHORED and not model_ancestor(i)}
print("\n== model steps inside exact lineages ==")
print("exact attempts with lineage", len(lineage), "| model-free exact with a model ancestor",
      len(via_model), f"({len({*fn_via_model} - fn_model_free_clean)} functions reachable no other way)")

# --- 4. per-state: did the model beat every model-free sibling from the same parent? -----------
unique = contested = 0
by_rel = collections.Counter()
for p, cs in kids.items():
    if p not in att or not att[p][2]:
        continue
    base = att[p][1]
    m = [att[c][1] for c in cs if c in att and att[c][2] and c in AUTHORED]
    d = [att[c][1] for c in cs if c in att and att[c][2] and not is_model(att[c][4])]
    if not m or max(m) <= base:
        continue
    if not d:
        unique += 1
    elif max(m) > max(d):
        contested += 1
print("\n== parents where a model child improved ==")
print("no model-free sibling tried", unique, "| model beat every model-free sibling", contested)

# --- 5. where do unresolved functions sit, and did the model ever move them? ------------------
done = set(first)
unsolved = {a for a in name if a not in done}
touched = collections.Counter()
for p, c, rel in edges:
    if c in att and p in att and att[c][0] in unsolved and att[p][2] and att[c][2] \
            and att[c][1] > att[p][1]:
        touched["model" if c in AUTHORED else "model-free"] += 1
moved = collections.defaultdict(set)
for p, c, rel in edges:
    if c in att and p in att and att[c][0] in unsolved and att[p][2] and att[c][2] \
            and att[c][1] > att[p][1]:
        if is_model(att[c][4]) and c not in AUTHORED:
            continue
        moved["model" if c in AUTHORED else "model-free"].add(att[c][0])
print("\n== unsolved functions ==")
print("unsolved", len(unsolved), "| improved by model-free", len(moved["model-free"]),
      "| by model", len(moved["model"]), "| ONLY by model", len(moved["model"] - moved["model-free"]),
      "| never improved by anything", len(unsolved - moved["model"] - moved["model-free"]))

# --- 6. the model-only exact functions: had model-free search tried them? -------------------
tried = collections.Counter()
for fid, a in att.items():
    if a[0] in only_model and fid not in AUTHORED and not is_model(a[4]):
        tried[a[0]] += 1
print("\n== model-only exact functions ==")
print(len(only_model), "functions |", sum(1 for f in only_model if tried[f]),
      "had model-free attempts (median", sorted(tried[f] for f in only_model)[len(only_model) // 2], ")")

# --- 7. what kind of edit wins: size of the model's improving edits vs model-free ones ------
import difflib
src = {}
def source(i):
    if i not in src:
        src[i] = (db.execute("select source_code from attempts where id=?", (i,)).fetchone()[0] or "")
    return src[i]
sizes = collections.defaultdict(list)
for p, c, rel in edges:
    if p in att and c in att and att[p][2] and att[c][2] and att[c][1] > att[p][1]:
        side = "model" if c in AUTHORED else (None if is_model(att[c][4]) else "model-free")
        if side is None or (side == "model-free" and len(sizes[side]) >= 3000):
            continue
        a, b = source(p).splitlines(), source(c).splitlines()
        changed = sum(1 for line in difflib.unified_diff(a, b, lineterm="", n=0)
                      if line[:1] in "+-" and not line.startswith(("+++", "---")))
        sizes[side].append(changed)
print("\n== changed lines in improving edits (model-free sampled) ==")
for side, v in sizes.items():
    v.sort()
    print(f"{side:10s} n={len(v)} median {v[len(v)//2]} p75 {v[int(len(v)*.75)]} p90 {v[int(len(v)*.9)]}"
          f" | >20 lines {sum(1 for x in v if x > 20)/len(v):.0%}")
