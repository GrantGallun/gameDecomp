"""Fixed D32 identity inference from binary observations only.

The D32 threshold was historically chosen on experimental FIT labels. This
module neither loads those labels nor changes policy per target.
"""
from __future__ import annotations

import collections


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


def solve(rows):
    variant, hub = "D", 0x20  # Fixed D32, historically FIT-selected.
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


