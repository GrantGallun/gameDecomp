"""Does a plateau look like edits that leave the residual unchanged? (read-only probe, dev data only)

For each attempt with a parent in the same function, compare diff fingerprints. 'null edit' = child
diff identical to parent's (zero information from that compile). Compares the first 20 edits of
functions that eventually reached exact against those that never did. Sealed functions and the
TU-mates of sealed functions are excluded: thresholds set here must not see the sealed side.
"""
import hashlib, json, random, re, sqlite3, statistics, sys
sys.path.insert(0, '/mnt/c/Code/gameDecomp')
from eval import seal
SEALED, SEALED_TUS = seal.sealed_in_sets(), seal.sealed_tus_in_sets()
db = sqlite3.connect('file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro', uri=True)


def fp_coarse(d):
    """Residual SHAPE: sorted multiset of (sign, mnemonic) over changed lines; offsets/regs/addresses ignored."""
    toks = sorted((l[0], l[1:].split()[0]) for l in d.splitlines() if l[:1] in '+-' and l[1:].split())
    return hashlib.md5(repr(toks).encode()).hexdigest()


def fp(d):
    lines = [re.sub(r'\s+', ' ', re.sub(r'0x[0-9a-f]+|\b[0-9a-f]{4,}\b', 'N', l.strip())) for l in d.splitlines() if l[:1] in '+-']
    return hashlib.md5('\n'.join(lines).encode()).hexdigest()


pool = [n for n, tu in db.execute("select f.name, t.name from functions f join tus t on t.id=f.tu_id")
        if n not in SEALED and tu not in SEALED_TUS]
random.Random(20261002).shuffle(pool)
by = {}
for name in pool[:600]:
    for cid, exact, cd, pd in db.execute(
            """select c.id, c.exact, c.diff_summary, p.diff_summary from attempts c
               join attempts p on p.id=c.parent_attempt_id join functions f on f.addr=c.func_addr
               where f.name=? and c.compiled=1 and p.compiled=1 and c.diff_summary is not null
                 and p.diff_summary is not null order by c.id""", (name,)):
        by.setdefault(name, []).append((cid, exact, fp_coarse(cd) == fp_coarse(pd)))
solved, unsolved, allnull = [], [], []
for name, seq in by.items():
    allnull += [n for *_, n in seq]
    if len(seq) < 20:
        continue
    rate = sum(n for *_, n in seq[:20]) / 20
    (solved if any(e for _, e, _ in seq) else unsolved).append(rate)


def s(x):
    return {'n': len(x), 'mean': round(statistics.mean(x), 3), 'median': round(statistics.median(x), 3)} if x else {'n': 0}


print(json.dumps({'sampled_functions': len(by), 'edges': len(allnull),
                  'overall_same_shape_rate': round(sum(allnull) / max(1, len(allnull)), 3),
                  'first20_same_shape_rate_eventually_exact': s(solved), 'first20_same_shape_rate_never_exact': s(unsolved)}))
