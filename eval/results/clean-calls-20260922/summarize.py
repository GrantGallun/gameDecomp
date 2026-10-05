"""Publish source-bound stage inventories and audit the completed replay."""
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.intake_blockers import inventory
from eval.intake_search import _preserves, _quality

OUT = Path(__file__).resolve().parent


def load(name):
    return json.loads((OUT / name).read_text())


def save(name, data):
    (OUT / name).write_text(json.dumps(data, indent=2) + '\n')


def totals(rows):
    return dict(functions=len(rows), ido_compiled=sum(r['verdict']['compiled'] for r in rows),
        frontend_passed=sum(r['frontend']['status'] == 'passed' for r in rows),
        both=sum(r['verdict']['compiled'] and r['frontend']['status'] == 'passed' for r in rows),
        exact=sum(r['verdict']['exact'] for r in rows),
        errors=sum(r['frontend']['error_count'] for r in rows))


baseline = load('baseline-reviewed.json')
paired = load('paired-confirmed.json')
assert len(baseline['rows']) == len(paired['rows']) == 200
before = {r['function']: r for r in baseline['rows']}
after = {r['function']: r for r in paired['rows']}
objects = load('object-replay.json')
for row in objects['rows']:
    prior = after[row['function']]
    if _preserves(row, prior) and _quality(row) > _quality(prior):
        after[row['function']] = row
assert set(before) == set(after)
losses = [n for n in before if not _preserves(after[n], before[n])]
assert not losses, losses
for row in [*before.values(), *after.values()]:
    assert hashlib.sha256(row['source'].encode()).hexdigest() == row['source_sha256']
    assert row['frontend']['status'] != 'unavailable'
    assert not row['frontend'].get('errors_truncated')
for name, row in after.items():
    folder = OUT / 'states-confirmed' / name
    (folder / 'final.c').write_text(row['source'])
    save(str((folder / 'final-frontend.json').relative_to(OUT)), row['frontend'])
save('final.json', dict(rows=list(after.values()), expected=200))
pre, post = inventory(list(before.values())), inventory(list(after.values()))
save('inventory-before-reviewed.json', pre)
save('inventory-after.json', post)
stops = Counter(r['stop_reason'] for r in paired['rows'])
crashes = [dict(function=r['function'], **t) for r in paired['rows'] for t in r['trace'] if t.get('status') == 'crashed']
assert not crashes, crashes
summary = dict(before=totals(list(before.values())), composition=totals(paired['rows']), after=totals(list(after.values())),
    object_attempts=objects['attempts'], object_states_examined=len(objects['rows']),
    ratchet_losses=losses, search_stops=dict(stops), search_attempts=sum(r['attempts'] for r in paired['rows']),
    new_exact=[n for n in before if after[n]['verdict']['exact'] and not before[n]['verdict']['exact']],
    fewer_errors=sum(after[n]['frontend']['error_count'] < before[n]['frontend']['error_count'] for n in before),
    changed_sources=sum(after[n]['source_sha256'] != before[n]['source_sha256'] for n in before),
    new_both=[n for n in before if after[n]['verdict']['compiled'] and after[n]['frontend']['status'] == 'passed' and not (before[n]['verdict']['compiled'] and before[n]['frontend']['status'] == 'passed')],
    remaining_frontend=post['frontend_classes'], remaining_objects=post['object_classes'],
    evidence=paired['evidence'])
with sqlite3.connect(f"file:{paired['attempt_db']}?mode=ro", uri=True) as conn:
    attempts = conn.execute('SELECT id,func_addr,source_code,source_sha256,parent_attempt_id,compiled,score,exact FROM attempts').fetchall()
    by_id = {r[0]: r for r in attempts}
    assert all(hashlib.sha256(r[2].encode()).hexdigest() == r[3] for r in attempts)
    edges = conn.execute('SELECT parent_attempt_id,child_attempt_id FROM attempt_edges').fetchall()
    assert all(by_id[p][1] == by_id[c][1] and by_id[c][4] == p for p, c in edges)
    assert sum(r[4] is not None for r in attempts) == len(edges)
    for row in [*before.values(), *after.values()]:
        logged = by_id[row['verdict']['receipt_id']]
        assert logged[3] == row['source_sha256']
        assert (bool(logged[5]), logged[6], bool(logged[7])) == (
            row['verdict']['compiled'], row['verdict']['score'], row['verdict']['exact'])
    node_count = 0
    for row in paired['rows']:
        for node in row['nodes']:
            logged = by_id[node['verdict']['receipt_id']]
            assert hashlib.sha256(node['source'].encode()).hexdigest() == node['source_sha256'] == logged[3]
            assert (bool(logged[5]), logged[6], bool(logged[7])) == (node['verdict']['compiled'], node['verdict']['score'], node['verdict']['exact'])
            if node['parent'] is not None:
                assert logged[4] is not None and by_id[logged[4]][3] == node['parent']
            node_count += 1
    summary['audited_search_nodes'] = node_count
    summary['logged_attempts_all_experiments'] = len(attempts)
    summary['logged_attempts_by_run'] = dict(conn.execute('SELECT run_id,count(*) FROM attempts GROUP BY run_id'))
    save('attempt-log-check.json', dict(attempts=len(attempts), source_hashes_valid=True,
        parent_edges=len(edges), parent_edges_valid=True, final_verdicts_bound_to_source=True, database=paired['attempt_db']))
modules = ('eval/intake_search.py', 'eval/intake_probe.py', 'eval/intake_runners.py',
           'eval/tool_agent_run.py', 'solver/frontend_fixits.py', 'solver/frontend_diagnostics.py',
           'solver/frontend_repair.py', 'solver/call_arity_repair.py', 'solver/project_headers.py',
           'solver/type_transaction.py', 'solver/buildtypes.py', 'solver/dataflow.py')
bound = {p: dict(recorded=paired['code_sha256'][p], current=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()) for p in modules}
for p, sha in objects['code_sha256'].items():
    bound[p] = dict(recorded=sha, current=hashlib.sha256((ROOT/p).read_bytes()).hexdigest())
assert all(v['recorded'] == v['current'] for v in bound.values())
save('code-verification.json', dict(all_measured_implementations_match=True, files=bound))
summary['sole_frontend_classes'] = dict(Counter(next(iter(r['classes'])) for r in post['rows'] if len(r['classes']) == 1))
save('summary.json', summary)

print(json.dumps({k:v for k,v in summary.items() if k not in ('remaining_frontend', 'remaining_objects', 'logged_attempts_by_run')}, indent=2))
