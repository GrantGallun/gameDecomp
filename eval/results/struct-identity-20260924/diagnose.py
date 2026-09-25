"""Development diagnostics on the FIT half only (labels used to understand edges, never as input to the pass).

1. Direct call edges from a labeled slot into a callee parameter: how often are the callee parameter's senders all
   the same label (a type-specific helper) versus mixed (a generic helper)? And can an UNLABELED signal tell them
   apart (the callee's own deepest access offset on that parameter; its fan-in)?
2. Callback co-passing: a call passing a function address g together with a pointer x. Does P(g,0)'s label equal x's?
3. Function addresses stored into an object's field: `sw &g, o(x)` -> does P(g,0) share x's label?
"""
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402

rows = json.loads((score.E / "facts.json").read_text())
lab, structs = score.labels(rows)
fit = {k: v for k, v in lab.items() if score.half(k[1]) == "FIT"}
name_of = {r["addr"]: r["function"] for r in rows}
tup = score.tup

# callee parameter footprint (max offset accessed directly on P(callee,k))
foot = collections.defaultdict(list)
for r in rows:
    for node, off, *_ in r["accesses"]:
        n = tup(node)
        if n[0] == "P":
            foot[(n[1], n[2])].append(off)

if len(sys.argv) > 1:
    pass
# 1. senders per callee param
senders = collections.defaultdict(list)
for r in rows:
    for tgt, k, x in r["calls"]:
        x = tup(x)
        if tgt in name_of and x in fit:
            senders[(name_of[tgt], k)].append(fit[x])
spec = gen = 0
rows1 = []
for cp, labs in senders.items():
    kinds = set(labs)
    callee_label = lab.get(("P", cp[0], cp[1]))
    specific = len(kinds) == 1
    spec += specific
    gen += not specific
    rows1.append({"callee": cp, "sender_labels": dict(collections.Counter(labs)), "callee_label": callee_label,
                  "max_off": max(foot[cp]) if foot[cp] else None, "n_access": len(foot[cp])})
print("1. callee params receiving labeled FIT senders:", len(senders), "specific", spec, "mixed", gen)
mixed = [x for x in rows1 if len(x["sender_labels"]) > 1]
print("   mixed examples:", [(x["callee"][0], x["sender_labels"], x["max_off"]) for x in mixed[:6]])
# does max offset separate? (generic helpers touch little)
for thr in (0, 0x10, 0x20, 0x40, 0x80):
    s_ok = sum(1 for x in rows1 if len(x["sender_labels"]) == 1 and (x["max_off"] or -1) >= thr)
    g_ok = sum(1 for x in rows1 if len(x["sender_labels"]) > 1 and (x["max_off"] or -1) >= thr)
    print(f"   keep edges when callee max_off >= {thr:#x}: specific kept {s_ok}, mixed kept {g_ok}")

# 2. callback co-passing
func_addrs = set(name_of)
by_site = collections.defaultdict(list)
for r in rows:
    for tgt, k, x in r["calls"]:
        by_site[(r["function"], id(r), tgt, len(by_site))].append(None)
agree = disagree = unlabeled = 0
examples = []
for r in rows:
    # group call args per site: calls are appended per site in k order; rebuild sites by scanning
    sites, cur, last_k = [], [], -1
    for tgt, k, x in r["calls"]:
        if k <= last_k:
            sites.append(cur)
            cur = []
        cur.append((tgt, k, tup(x)))
        last_k = k
    if cur:
        sites.append(cur)
    for site in sites:
        fns = [x[1] for _t, _k, x in site if x[0] == "G" and x[1] in func_addrs]
        ptrs = [x for _t, _k, x in site if x[0] != "G"]
        for g in fns:
            gl = lab.get(("P", name_of[g], 0))
            for x in ptrs:
                xl = fit.get(x)
                if xl is None or gl is None or score.half(name_of[g]) != "FIT":
                    unlabeled += 1
                    continue
                if gl == xl:
                    agree += 1
                else:
                    disagree += 1
                    if len(examples) < 6:
                        examples.append((r["function"], name_of[g], xl, gl))
print("2. callback co-passing (fn address + pointer in one call): agree", agree, "disagree", disagree,
      "unlabeled", unlabeled, examples)


def self_registration():
    """FIT: call sites passing function address(es) g but NO trackable pointer: does P(g,0) share P(caller,0)'s label?"""
    agree = disagree = unl = 0
    ex = []
    for r in rows:
        for site in score.call_sites(r):
            fns = [x[1] for _t, _k, x in site if x[0] == "G" and x[1] in name_of]
            ptrs = [x for _t, _k, x in site if x[0] not in ("G", "E")]
            if not fns or ptrs:
                continue
            cl = fit.get(("P", r["function"], 0))
            for g in fns:
                gl = fit.get(("P", name_of[g], 0))
                if cl is None or gl is None:
                    unl += 1
                elif cl == gl:
                    agree += 1
                else:
                    disagree += 1
                    if len(ex) < 8:
                        ex.append((r["function"], name_of[site[0][0]], name_of[g], cl, gl))
    print("3. fn-address-only sites, callback shares caller's P0 label: agree", agree, "disagree", disagree,
          "unlabeled", unl, ex)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "self":
    self_registration()
