import json, sqlite3, sys, time
from dataclasses import asdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace, rng_alternatives

out = ROOT / 'eval/results/swarm-rng-v1'
out.mkdir(exist_ok=True)
source = (ROOT / 'eval/results/last-push-final/__MusIntRandom.c').read_text()
repo = Path('/home/grant/decomp/sbk1')
ws = repo / 'nonmatchings/__MusIntRandom'
conn = sqlite3.connect(ROOT / 'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite')
rows = []
for i, variant in enumerate(rng_alternatives.candidates(source, '__MusIntRandom')):
    tag = f'__MusIntRandom_swarm_rng_{time.time_ns()}'
    attempt = workspace.score(ws, repo, tag, variant.source, conn=conn, func='__MusIntRandom')
    row = dict(index=i, label=variant.label, tag=tag, **asdict(attempt))
    rows.append(row)
    (out / f'{i}.c').write_text(variant.source)
    (out / 'scores.json').write_text(json.dumps(rows, indent=2))
    print(i, variant.label, attempt.compiled, attempt.score, attempt.exact, flush=True)
    if attempt.exact:
        break
