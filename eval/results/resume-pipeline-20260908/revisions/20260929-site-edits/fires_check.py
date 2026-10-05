"""Silent-decline check in the STAGED FROZEN tree: the route this amendment ships fires there.

Rows (apply_amendment.py refuses unless every row is ok):
  propose     site_edits.propose finds the lui/ori literal fix on updateEndingJamSlideLeftToMarker's fixture
  copydir     site_edits.propose offers the copy-direction rewrite on spawnEndingCreditsSmallBurst's fixture
  scheduled   the staged repair_queue.next_profile picks a site_edits@ profile for a real pending small node
  end_to_end  eval.site_edit_repair.run, on an isolated repo copy and a scratch database holding only that
              node's retained attempt, returns a certified exact result (no campaign file is touched)

    PYTHONPATH=<staged project> python fires_check.py <staged project>
"""
import json
import sqlite3
import sys
from pathlib import Path

project = Path(sys.argv[1])
sys.path.insert(0, str(project))
from eval import campaign_state, campaign_workers, completion_campaign, site_edit_repair  # noqa: E402
from solver import repair_queue, site_edits  # noqa: E402

for module in (site_edits, site_edit_repair, repair_queue, completion_campaign):
    assert Path(module.__file__).resolve().is_relative_to(project.resolve()), module.__file__
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
WORK = Path('/home/grant/decomp/experiments/site-edits-stage-20260929/fires')
rows = []
fixtures = json.loads((project / 'tests/fixtures/site_edits_fires.json').read_text())

case = fixtures['updateEndingJamSlideLeftToMarker']
edits, _ = site_edits.propose(case['source'], 'updateEndingJamSlideLeftToMarker', case['diff'], case['attribution'])
rows.append({'check': 'propose', 'ok': any('< -0x7FFFFF' in e.apply(case['source']) for e in edits)})

case = fixtures['spawnEndingCreditsSmallBurst']
edits, _ = site_edits.propose(case['source'], 'spawnEndingCreditsSmallBurst', case['diff'], case['attribution'])
rows.append({'check': 'copydir', 'ok': any(e.kind == 'copydir' for e in edits)})

state = campaign_state.read(NATIVE / 'campaign.json')
name = next(n for n in ('spawnEndingCreditsSmallBurst', 'spawnEndingCreditsPhaseAdvanceSparkle',
                        'updateEndingJamSlideLeftToMarker')
            if state['nodes'].get(n, {}).get('status') == 'pending'
            and repair_queue.site_edit_profile(state['nodes'][n]) is not None)
node = state['nodes'][name]
chosen = repair_queue.next_profile(node, 0, completion_campaign.PROFILES)
rows.append({'check': 'scheduled', 'function': name, 'profile': (chosen or {}).get('name'),
             'ok': bool(chosen) and chosen['name'].startswith('site_edits@')})

WORK.mkdir(parents=True, exist_ok=True)
iso = campaign_workers.isolate(Path('/home/grant/decomp/sbk1'), WORK / name, name)
db = WORK / f'{name}.sqlite'
db.unlink(missing_ok=True)
conn = sqlite3.connect(db)
conn.executescript((project / 'kb/schema.sql').read_text())
conn.execute('ATTACH DATABASE ? AS c', ((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro',))
for table in ('extraction', 'tus', 'functions'):
    conn.execute(f'INSERT INTO main.{table} SELECT * FROM c.{table}')
conn.execute('INSERT OR IGNORE INTO main.attempt_runs SELECT r.* FROM c.attempt_runs r '
             'JOIN c.attempts a ON a.run_id = r.id WHERE a.id = ?', (node['attempt_id'],))
columns = [r[1] for r in conn.execute('PRAGMA main.table_info(attempts)')]
listed = ','.join(columns)
selected = ','.join('NULL' if c == 'parent_attempt_id' else c for c in columns)
conn.execute(f'INSERT INTO main.attempts ({listed}) SELECT {selected} FROM c.attempts WHERE id=?',
             (node['attempt_id'],))
conn.commit()
conn.execute('DETACH DATABASE c')
conn.close()
result = site_edit_repair.run(repo=iso, db=db, function=name, node=node, out=WORK / f'{name}.result.json')
rows.append({'check': 'end_to_end', 'function': name, 'exact': result.get('exact'),
             'compiles': result.get('proposal_compiles'), 'ok': result.get('exact') is True})
print(json.dumps(rows, indent=1))
if not all(r['ok'] for r in rows):
    raise SystemExit('a shipped mechanism does not fire in the staged frozen tree')
