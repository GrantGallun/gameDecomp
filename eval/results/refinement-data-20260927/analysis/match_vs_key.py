"""How different are our matches from the reference decomp? TOOL DEVELOPMENT ONLY (key-distance rule).

Per matched function: the distance of its CLOSEST exact attempt to the reference (if even the
closest is far, the function was matched by genuinely different C) and of its first exact.
Distance = key_distance.canonical (alpha-renamed tokens), normalized Levenshtein.
Writes examples of the farthest matches for reading by eye.

    ~/decomp/sbk1/.venv/bin/python match_vs_key.py [examples_out]   (cwd holding campaign.sqlite)
"""
import collections
import sqlite3
import sys
from pathlib import Path

from rapidfuzz.distance import Levenshtein

sys.path.insert(0, "/mnt/c/Code/gameDecomp")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from key_distance import REFERENCE, canonical  # noqa: E402
from patterns.commit_provenance import function_definitions  # noqa: E402

refs = {}
for path in REFERENCE.rglob("*.c"):
    try:
        for name, text in function_definitions(path.read_text(errors="replace")).items():
            refs.setdefault(name, text)
    except Exception:
        continue
db = sqlite3.connect("file:campaign.sqlite?mode=ro", uri=True)
rows = db.execute("select f.name, a.id, a.source_code, coalesce(a.strategy,'') from attempts a "
                  "join functions f on f.addr=a.func_addr where a.exact=1 order by a.id").fetchall()
per = collections.defaultdict(list)
for name, aid, src, strategy in rows:
    if name not in refs:
        continue
    c, k = canonical(src, name), canonical(refs[name])
    if c is None or k is None:
        continue
    per[name].append((Levenshtein.normalized_distance(c, k), aid, strategy, src, len(k)))

bands = [(0, 0, "identical"), (1e-9, 0.1, "<0.1"), (0.1, 0.3, "0.1-0.3"), (0.3, 0.5, "0.3-0.5"),
         (0.5, 0.7, "0.5-0.7"), (0.7, 1.01, ">=0.7")]
for label, pick in (("closest exact", min), ("first exact", lambda v: v[0])):
    ds = [pick(v)[0] for v in per.values()]
    print(f"{label}, {len(ds)} functions:")
    for lo, hi, name in bands:
        n = sum(1 for d in ds if (d == 0 if hi == 0 else lo <= d < hi))
        print(f"  {name:10s} {n:5d}  {n / len(ds):6.1%}")
closest = sorted(((min(v), n) for n, v in per.items()), reverse=True)
by_strategy = collections.Counter(min(v)[2].split(":")[0] for v in per.values() if min(v)[0] == 0)
print("\nstrategies of the identical ones:", by_strategy.most_common(6))
print("size (reference tokens) by band of closest exact:")
for lo, hi, name in bands:
    sz = sorted(min(v)[4] for v in per.values()
                if (min(v)[0] == 0 if hi == 0 else lo <= min(v)[0] < hi))
    if sz:
        print(f"  {name:10s} median {sz[len(sz) // 2]} tokens")
if len(sys.argv) > 1:
    with open(sys.argv[1], "w") as out:
        for (d, aid, strategy, src, _), name in closest[:12]:
            out.write(f"===== {name}  distance {d:.3f}  attempt {aid}  {strategy}\n--- MATCH\n{src.strip()}\n"
                      f"--- REFERENCE\n{refs[name].strip()}\n\n")
