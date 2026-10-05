"""Corrected plateau probes (dev data only; sealed functions and their TU-mates excluded).

v1 hashed the ---/+++ header lines (filename + timestamp), so every compile looked different (bug found by
Opus review). v2:
  A. no-op rate with the header stripped (solver.branch_points.diff_body), reverify/same-source rows dropped,
     split by strategy family, solved vs never-exact.
  B. hazard: P(function reaches exact within the next 50 compiled attempts | k = attempts since best-so-far last rose),
     over positions of functions that were not yet exact. Flat hazard => no restart/stop trigger exists in this signal.
"""
import collections, hashlib, json, random, re, sqlite3, sys
sys.path.insert(0, '/mnt/c/Code/gameDecomp')
from eval import seal
from solver.branch_points import diff_body

SEALED, SEALED_TUS = seal.sealed_in_sets(), seal.sealed_tus_in_sets()
db = sqlite3.connect('file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro', uri=True)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1200


def norm(d):
    return hashlib.md5(re.sub(r'\s+', ' ', diff_body(d)).encode()).hexdigest()


def family(strategy):
    return re.split(r'[:\-/ ]', strategy or 'none')[0][:24]


pool = [n for n, tu in db.execute("select f.name, t.name from functions f join tus t on t.id=f.tu_id")
        if n not in SEALED and tu not in SEALED_TUS]
random.Random(20261003).shuffle(pool)

edge = collections.defaultdict(lambda: [0, 0])          # (family, solved) -> [noop, n]
hazard = collections.defaultdict(lambda: [0, 0])        # k bucket -> [reached exact within 50, positions]
paired = {'low': [0, 0], 'high': [0, 0]}                # same functions at k<30 vs k>=100
paired_funcs = 0
funcs = 0
for name in pool[:N]:
    rows = db.execute("""select c.id, c.score, c.exact, c.strategy, c.source_sha256, c.parent_attempt_id, c.diff_summary
        from attempts c join functions f on f.addr=c.func_addr where f.name=? and c.compiled=1 order by c.id""",
                      (name,)).fetchall()
    if len(rows) < 30:
        continue
    funcs += 1
    solved = any(r[2] == 1 for r in rows)
    byid = {r[0]: r for r in rows}
    for cid, score, exact, strat, sha, parent, diff in rows:
        p = byid.get(parent)
        if not p or not diff or not p[6] or 'reverify' in (strat or '') or (sha and sha == p[4]):
            continue
        cell = edge[(family(strat), solved)]
        cell[1] += 1
        cell[0] += norm(diff) == norm(p[6])
    # hazard over attempts before the first exact
    exact_at = next((i for i, r in enumerate(rows) if r[2] == 1), None)
    best, since = -1.0, 0
    local = {'low': [0, 0], 'high': [0, 0]}
    for i, r in enumerate(rows):
        if exact_at is not None and i >= exact_at:
            break
        if (r[1] or 0) > best + 1e-9:
            best, since = r[1] or 0, 0
        else:
            since += 1
        bucket = min(since // 10 * 10, 100)
        h = hazard[bucket]
        h[1] += 1
        h[0] += exact_at is not None and exact_at - i <= 50
        side = 'low' if since < 30 else 'high' if since >= 100 else None
        if side:
            local[side][1] += 1
            local[side][0] += exact_at is not None and exact_at - i <= 50
    if local['low'][1] and local['high'][1]:
        paired_funcs += 1
        for side in paired:
            paired[side][0] += local[side][0]; paired[side][1] += local[side][1]

fams = collections.defaultdict(dict)
for (fam, solved), (noop, n) in edge.items():
    fams[fam]['solved' if solved else 'never'] = {'n': n, 'noop': round(noop / n, 3) if n else None}
top = sorted(fams.items(), key=lambda kv: -sum(v['n'] for v in kv[1].values()))[:8]
print(json.dumps({'functions_30plus': funcs,
                  'within_function_control': {'functions_with_both': paired_funcs, **{k: {'positions': v[1], 'p': round(v[0] / v[1], 4) if v[1] else None} for k, v in paired.items()}},
                  'noop_rate_by_family_top8': dict(top),
                  'hazard_P_exact_within_50_by_edits_since_best_rose': {
                      str(k): {'positions': v[1], 'p': round(v[0] / v[1], 4)} for k, v in sorted(hazard.items())}}, indent=1))
