"""Silent-decline check in the STAGED FROZEN tree: the mined lane this amendment ships actually fires there.

`site_edits._mined_edits` swallows every exception, so a missing module or table in the frozen tree would look exactly
like a function with nothing to propose. Rows (apply_amendment.py refuses unless every row is ok):
  table      solver.rule_miner.Table.load() finds patterns/mined_rules.json in the staged tree and has live rules
  scheduled  the staged repair_queue.next_profile picks site_edits at budget 72 for a real pending small node
  mined      site_edit_repair.run, on an isolated repo copy and a scratch database holding only that node's retained
             attempt, compiles at least one `mined:` candidate (nodes are tried in order until one does; at most 6)

    PYTHONPATH=<staged project> python fires_check.py <staged project>
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

project = Path(sys.argv[1])
sys.path.insert(0, str(project))
from eval import campaign_state, campaign_workers, completion_campaign, site_edit_repair  # noqa: E402
from solver import repair_queue, rule_miner, site_edits  # noqa: E402

for module in (site_edits, site_edit_repair, repair_queue, rule_miner):
    assert Path(module.__file__).resolve().is_relative_to(project.resolve()), module.__file__
HERE = Path(__file__).resolve().parent
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
WORK = Path('/home/grant/decomp/experiments/mined-lane-stage-20260930/fires')
rows = []

table = rule_miner.Table.load()
live = [k for k, v in (table.rules.items() if table is not None and hasattr(table, 'rules') else []) if not v.get('pruned')]
rows.append({'check': 'table', 'rules': len(live), 'ok': table is not None and len(live) > 0})

state = campaign_state.read(NATIVE / 'campaign.json')
names = [n for n, node in sorted(state['nodes'].items())
         if node.get('status') == 'pending' and repair_queue.site_edit_profile(node) is not None]
rows.append({'check': 'candidates', 'count': len(names), 'ok': bool(names)})
chosen = repair_queue.next_profile(state['nodes'][names[0]], 0, completion_campaign.PROFILES) if names else None
rows.append({'check': 'scheduled', 'function': names[0] if names else None, 'profile': (chosen or {}).get('name'),
             'budget': (chosen or {}).get('site_edit_budget'),
             'ok': bool(chosen) and chosen['name'].startswith('site_edits@') and chosen.get('site_edit_budget') == 72})

WORK.mkdir(parents=True, exist_ok=True)
tried = []
fired = False
for name in names[:6]:
    node = state['nodes'][name]
    iso = campaign_workers.isolate(Path('/home/grant/decomp/sbk1'), WORK / name, name)
    db = WORK / f'{name}.sqlite'
    db.unlink(missing_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript((project / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS c', ((NATIVE / 'campaign.sqlite').resolve().as_uri() + '?mode=ro',))
    for t in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{t} SELECT * FROM c.{t}')
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
    result = site_edit_repair.run(repo=iso, db=db, function=name, node=node, out=WORK / f'{name}.result.json', budget=72)
    trail = (WORK / f'{name}.result.trail.json').read_text()
    mined = trail.count('mined:')
    tried.append({'function': name, 'compiles': result.get('proposal_compiles'), 'mined_in_trail': mined,
                  'exact': result.get('exact')})
    if mined:
        fired = True
        break
rows.append({'check': 'mined', 'tried': tried, 'ok': fired})

ok = all(r['ok'] for r in rows)
manifest = hashlib.sha256((HERE / 'stage.json').read_bytes()).hexdigest()
(HERE / 'fires-result.json').write_text(json.dumps({'manifest_sha256': manifest, 'ok': ok, 'rows': rows}, indent=1) + '\n')
print(json.dumps(rows, indent=1))
if not ok:
    raise SystemExit(1)
