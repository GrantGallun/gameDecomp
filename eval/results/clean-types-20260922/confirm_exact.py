"""Independent native workspaces for each new exact cohort result."""
import hashlib
import json
import sqlite3
from pathlib import Path
from replay import OUT, REPO, NATIVE, campaign_workers, workspace, frontend_diagnostics, _attempt_to_verdict


rows = json.loads((OUT / 'final.json').read_text())['rows']
assert len(rows) == 200
baselines = {r['function']: r for r in json.loads((OUT / 'baseline-reviewed.json').read_text())['rows']}
conn = sqlite3.connect(NATIVE / 'attempts.sqlite')
research = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
reports = []
for row in rows:
    if not row['verdict']['exact'] or baselines[row['function']]['verdict']['exact']:
        continue
    name, source = row['function'], row['source']
    native = campaign_workers.isolate(REPO, NATIVE / 'confirmation-builds' / name, name)
    ws = native / 'nonmatchings' / name
    att = workspace.score(ws, native, name, source, conn=conn, func=name,
        strategy='clean-types:independent-confirmation', model='zero-model',
        run_id='clean-types-20260922:confirmation', parent_attempt_id=row['verdict']['receipt_id'],
        relation='confirmation', action='independent-exact-confirmation')
    front = frontend_diagnostics.analyse(source, repo=native, target=baselines[name]['target'], full_diagnostics=True)
    verdict = _attempt_to_verdict(att)
    assert verdict['compiled'] and verdict['exact'] and front['status'] == 'passed', name
    previous = research.execute('SELECT count(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr '
        'WHERE f.name=? AND a.exact=1', (name,)).fetchone()[0]
    reports.append(dict(function=name, source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        verdict=verdict, frontend=front, prior_research_exact_attempts=previous))
    print(name, 'independently exact', 'prior research exact attempts:', previous, flush=True)
(OUT / 'exact-confirmation.json').write_text(json.dumps(reports, indent=2) + '\n')
conn.close()
research.close()
