"""Why does stmt_swap find no sites? Counts each gate on the real pool."""
import collections
import re

import run

c = collections.Counter()
ex = []
for r in run.pool():
    p = run.split(r["source"], r["function"])
    if not p:
        c["split"] += 1
        continue
    L = run._lines(p[1])
    idx = list(run._body_range(L))
    c["body-lines"] += len(idx)
    c["simple"] += sum(run._simple_stmt(L[i]) for i in idx)
    for a, b in zip(idx, idx[1:]):
        if run._simple_stmt(L[a]) and run._simple_stmt(L[b]):
            c["adjacent-simple"] += 1
            if re.search(rf"{run.ID}\s*\(", L[a] + L[b]):
                c["has-call"] += 1
            elif len(ex) < 8:
                ex.append((L[a], L[b]))
print(c)
for e in ex:
    print(e)
