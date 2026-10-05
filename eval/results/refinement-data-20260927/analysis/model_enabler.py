"""For model-free exacts with a model ancestor: what did the NEAREST model step do? Read-only.

Checks the census's 344-function claim is not an artefact of a model-made root draft.
"""
import collections
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1] if len(sys.argv) > 1 else 'campaign.sqlite'}?mode=ro",
                     uri=True)
FREE = ("", "zero-model", "deterministic", "none", "m2c")
att = {r[0]: r[1:] for r in db.execute(
    "select id, func_addr, score, coalesce(compiled,0), coalesce(exact,0), coalesce(model,''), "
    "strategy from attempts")}
parents = collections.defaultdict(list)
rel_of = {}
for p, c, rel in db.execute("select parent_attempt_id, child_attempt_id, relation from attempt_edges"):
    parents[c].append(p)
    rel_of[(p, c)] = rel
# Authorship, not the model label: reverify rows carry model='gpt-oss:20b' with no model output,
# and the first run of this script found all 445 "model ancestors" were such rows.
AUTHORED = {r[0] for r in db.execute(
    "select id from attempts where length(coalesce(raw_response,'')) > 0 "
    "and coalesce(model,'') not in ('','zero-model','deterministic','none','m2c')")}
labelled = lambda i: i in att and att[i][4] not in FREE
is_model = lambda i: i in AUTHORED


def nearest_model(node):
    """BFS up the DAG; returns (model_attempt, its_parent_or_None, depth) for the nearest."""
    frontier, seen, depth = [node], {node}, 0
    while frontier:
        depth += 1
        nxt = []
        for n in frontier:
            for p in parents.get(n, ()):
                if p in seen:
                    continue
                seen.add(p)
                if is_model(p):
                    gp = [g for g in parents.get(p, ()) if g in att]
                    return p, (max(gp, key=lambda g: att[g][1] or -1) if gp else None), depth
                nxt.append(p)
        frontier = nxt
    return None


kinds = collections.Counter()
per_fn = {}
depths = []
strat = collections.Counter()
for i, a in att.items():
    if not a[3] or labelled(i) or i not in parents:
        continue
    hit = nearest_model(i)
    if not hit:
        continue
    m, gp, depth = hit
    depths.append(depth)
    strat[(att[m][5] or "").split(":")[0]] += 1
    if gp is None:
        kind = "model step is a lineage ROOT (draft)"
    elif not att[gp][2] and att[m][2]:
        kind = "model made it COMPILE"
    elif att[gp][2] and att[m][2] and att[m][1] > att[gp][1]:
        kind = "model IMPROVED score"
    elif att[gp][2] and att[m][2] and att[m][1] == att[gp][1]:
        kind = "model step FLAT"
    elif att[gp][2] and att[m][2]:
        kind = "model step LOWER"
    else:
        kind = "model step did not compile"
    kinds[kind] += 1
    per_fn.setdefault(a[0], set()).add(kind)
print("model-free exacts with a model ancestor:", sum(kinds.values()), "in", len(per_fn), "functions")
for k, v in kinds.most_common():
    print(f"  {v:5d}  {k}")
print("depth from exact to nearest model step: median", sorted(depths)[len(depths) // 2],
      "| p90", sorted(depths)[int(len(depths) * .9)], "| depth 1:", depths.count(1))
print("nearest model step strategy:", strat.most_common(8))
