"""Solve identity facts per variant and score against the reference headers (answer key only). PROTOCOL.md.

    python3 score.py   -> score.json
"""
from __future__ import annotations

import collections
import hashlib
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from eval import ground_truth  # noqa: E402

E = Path.home() / "decomp/experiments/struct-identity-20260924"
REPO = Path.home() / "decomp/sbk1"
HUBS = (2, 3, 4, 6, 8, 12, 16, 24)


def tup(x):
    return tuple(tup(v) for v in x) if isinstance(x, list) else x


class UF:
    """Union-find over type variables with structural merge of D/E children."""

    def __init__(self):
        self.parent, self.children = {}, collections.defaultdict(dict)

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def add(self, x):
        """Register x (and its ancestors) as structural children of their parents' roots."""
        stack = [x]
        while stack:
            n = stack.pop()
            if n in self.parent:
                continue
            self.parent[n] = n
            if n[0] in ("D", "E"):
                stack.append(n[1])
                self.add(n[1])
                key = (n[0], n[2])
                pr = self.find(n[1])
                other = self.children[pr].get(key)
                if other is None:
                    self.children[pr][key] = n
                else:
                    self.union(other, n)

    def union(self, a, b):
        self.add(a)
        self.add(b)
        work = [(a, b)]
        while work:
            x, y = work.pop()
            rx, ry = self.find(x), self.find(y)
            if rx == ry:
                continue
            if len(self.children[rx]) < len(self.children[ry]):
                rx, ry = ry, rx
            self.parent[ry] = rx
            for key, child in self.children.pop(ry, {}).items():
                mine = self.children[rx].get(key)
                if mine is None:
                    self.children[rx][key] = child
                else:
                    work.append((mine, child))


def call_sites(r):
    """Group a function's call-edge rows back into sites (walk appends each site's args in k order)."""
    sites, cur, last = [], [], -1
    for tgt, k, x in r["calls"]:
        if k <= last or (cur and tgt != cur[0][0]):
            sites.append(cur)
            cur = []
        cur.append((tgt, k, tup(x)))
        last = k
    if cur:
        sites.append(cur)
    return sites


def solve(rows, variant: str, hub: int | None = None):
    name_of = {r["addr"]: r["function"] for r in rows}
    arity = {r["addr"]: set(r.get("arity_reads", range(4))) for r in rows}
    uf = UF()
    fan = collections.defaultdict(set)
    deref = collections.defaultdict(lambda: -1)      # max direct access offset on P(f,k); -1 = never dereferenced
    for r in rows:
        for tgt, k, x in r["calls"]:
            fan[(tgt, k)].add(r["function"])
        for node, off, *_ in r["accesses"]:
            n = tup(node)
            if n[0] == "P":
                deref[(n[1], n[2])] = max(deref[(n[1], n[2])], off)
    for r in rows:
        for a in r["accesses"]:
            uf.add(tup(a[0]))
        for k in range(4):
            uf.add(("P", r["function"], k))
        for a, b in r["unify"]:
            uf.union(tup(a), tup(b))
        for x in r["returns"]:
            uf.union(("R", r["addr"]), tup(x))
        if variant == "A":
            continue
        if variant == "D":
            for site in call_sites(r):
                tgt = site[0][0]
                site = [s for s in site if s[1] in arity.get(tgt, set(range(4)))]
                if not site:
                    continue
                for _t, k, x in site:
                    if tgt in name_of and x[0] != "G" and deref[(name_of[tgt], k)] >= hub:
                        uf.union(("P", name_of[tgt], k), x)
                fns = [x[1] for _t, _k, x in site if x[0] == "G" and x[1] in name_of]
                ptrs = list(dict.fromkeys(x for _t, _k, x in site if x[0] != "G" and x[0] != "E"))
                if fns and len(ptrs) == 1:
                    for g in fns:
                        uf.union(("P", name_of[g], 0), ptrs[0])
            continue
        for tgt, k, x in r["calls"]:
            if tgt not in name_of:
                continue
            if variant == "C" and len(fan[(tgt, k)]) >= hub:
                continue
            uf.union(("P", name_of[tgt], k), tup(x))
    return uf


def labels(rows):
    structs, sigs = ground_truth.load(REPO)
    present = {r["function"] for r in rows}
    out = {}
    for fn, ps in sigs.items():
        if fn not in present:
            continue
        for k, s in enumerate(ps[:4]):
            if s and s in structs:
                out[("P", fn, k)] = s
    return out, structs


def half(fn: str) -> str:
    return "FIT" if hashlib.sha256(fn.encode()).digest()[0] % 2 == 0 else "CHECK"


def bcubed(uf, lab, which):
    items = [i for i in lab if half(i[1]) == which]
    by_root = collections.defaultdict(list)
    for i in items:
        by_root[uf.find(i)].append(i)
    size = collections.Counter(lab[i] for i in items)
    p = r = 0.0
    per_struct = collections.defaultdict(list)
    for i in items:
        g = by_root[uf.find(i)]
        same = sum(lab[j] == lab[i] for j in g)
        p += same / len(g)
        r += same / size[lab[i]]
        per_struct[lab[i]].append(same / size[lab[i]])
    n = len(items)
    base_r = sum(1 / size[lab[i]] for i in items) / n
    prec, rec = p / n, r / n
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    return {"items": n, "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "baseline_recall": round(base_r, 4),
            "per_struct_recall": {s: round(sum(v) / len(v), 3) for s, v in per_struct.items() if size[s] >= 10}}


def layout(uf, rows, lab, structs, which):
    """Merged int observations of each majority-labeled root vs ground_truth.flatten."""
    obs = collections.defaultdict(list)
    for r in rows:
        for node, off, width, _s, _l, cls in r["accesses"]:
            if cls == "int" and width:
                obs[uf.find(tup(node))].append((off, width))
    members = collections.defaultdict(list)
    for i, s in lab.items():
        if half(i[1]) == which:
            members[uf.find(i)].append(s)
    agree = disagree = unchecked = 0
    for root, ss in members.items():
        if len(ss) < 2:
            continue
        s, c = collections.Counter(ss).most_common(1)[0]
        flat = ground_truth.flatten(s, structs)
        lay = flat[0] if isinstance(flat, tuple) else flat
        if not lay:
            unchecked += len(obs[root])
            continue
        spans = [(o, w) for o, ws in lay.items() for w in ws]
        for off, width in obs[root]:
            if width in lay.get(off, set()):
                agree += 1
            elif any(o <= off and off + width <= o + w for o, w in spans):
                agree += 1
            elif any(o <= off < o + w or off <= o < off + width for o, w in spans):
                disagree += 1
            else:
                unchecked += 1
    checked = agree + disagree
    return {"agree": agree, "disagree": disagree, "unchecked": unchecked,
            "agreement": round(agree / checked, 4) if checked else None}


def gain(uf, rows, lab, which):
    own = collections.defaultdict(set)
    group = collections.defaultdict(set)
    funcs = collections.defaultdict(set)
    for r in rows:
        for node, off, *_ in r["accesses"]:
            n = tup(node)
            own[n].add(off)
            group[uf.find(n)].add(off)
            funcs[uf.find(n)].add(r["function"])
    gains = [len(group[uf.find(i)] - own[i]) for i in lab if half(i[1]) == which and len(funcs[uf.find(i)]) > 1]
    return {"slots_in_multi_function_groups": len(gains),
            "median_extra_offsets": statistics.median(gains) if gains else 0,
            "mean_extra_offsets": round(statistics.mean(gains), 1) if gains else 0}


def edge_counts(rows):
    return {"accesses": sum(len(r["accesses"]) for r in rows), "calls": sum(len(r["calls"]) for r in rows),
            "store_unify": sum(len(r["unify"]) for r in rows), "returns": sum(len(r["returns"]) for r in rows)}


def main():
    rows = json.loads((E / "facts.json").read_text())
    lab, structs = labels(rows)
    out = {"facts": edge_counts(rows), "labeled_slots": len(lab),
           "fit_items": sum(half(i[1]) == "FIT" for i in lab), "check_items": sum(half(i[1]) == "CHECK" for i in lab)}
    fit = {}
    for v, hub in [("A", None), ("B", None)] + [("C", h) for h in HUBS] + [("D", t) for t in (0, 0x10, 0x20)]:
        uf = solve(rows, v, hub)
        fit[f"{v}{'' if hub is None else hub}"] = bcubed(uf, lab, "FIT")
    ok = {k: m for k, m in fit.items() if k[0] in "CD" and m["precision"] >= 0.90}
    chosen = max(ok, key=lambda k: ok[k]["f1"]) if ok else None
    out["fit"] = {k: {x: m[x] for x in ("precision", "recall", "f1", "baseline_recall")} for k, m in fit.items()}
    out["chosen_C"] = chosen
    check = {}
    for key in ["A", "B"] + ([chosen] if chosen else []):
        v, hub = key[0], (int(key[1:]) if key[1:] else None)
        uf = solve(rows, v, hub)
        check[key] = {"bcubed": bcubed(uf, lab, "CHECK"), "layout": layout(uf, rows, lab, structs, "CHECK"),
                      "gain": gain(uf, rows, lab, "CHECK")}
    out["check"] = check
    if chosen:
        pr = check[chosen]["bcubed"]["per_struct_recall"]
        out["fires"] = {"structs_ge10_slots_with_recall_ge_0.5": [s for s, r in pr.items() if r >= 0.5]}
        m, lay = check[chosen]["bcubed"], check[chosen]["layout"]
        out["verdict"] = ("gets us somewhere" if m["precision"] >= 0.90 and m["recall"] >= 0.50
                          and (lay["agreement"] or 0) >= 0.95 else
                          "partial" if m["precision"] >= 0.90 else "null")
    else:
        out["verdict"] = "null (no C variant reaches precision 0.90 on FIT)"
    (HERE / "score.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in out if k != "check"}, indent=1))
    for k, v in check.items():
        print(k, json.dumps({"bcubed": {x: v["bcubed"][x] for x in ("precision", "recall", "f1", "baseline_recall")},
                             "layout": v["layout"], "gain": v["gain"]}))


if __name__ == "__main__":
    main()
