"""Paired real replay from a closed private history snapshot, never the live DB."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

from eval import campaign_workers
from eval.semantic_lane import Panel
from solver import callee_execution as c, mips_differential as d, project_headers, workspace

out = Path(__file__).parent
work = Path('/home/grant/decomp/impact-shift-20260912')
work.mkdir(exist_ok=True)
original = Path('/home/grant/decomp/partial-popup-v3-20260912/history.sqlite')
db = work/'history.sqlite'
if not db.exists():
    shutil.copy2(original, db)
repo = Path('/home/grant/decomp/sbk1')
function = sys.argv[1] if len(sys.argv) > 1 else 'calculateFixedAngleFromDeltaXZ'
with sqlite3.connect(f'file:{original}?mode=ro', uri=True) as snapshot:
    cutoff = snapshot.execute('SELECT MAX(id) FROM attempts').fetchone()[0]
with sqlite3.connect(db) as conn:
    row = conn.execute('SELECT a.id,a.source_code,a.source_sha256,a.score FROM attempts a '
                       'JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.compiled=1 AND a.id<=? '
                       'ORDER BY a.id DESC LIMIT 1', (function, cutoff)).fetchone()
    if row is None:
        raise ValueError('no compiling source in private snapshot')
    parent_id, source, source_hash, prior_score = row
    assert hashlib.sha256(source.encode()).hexdigest() == source_hash
    private_repo = campaign_workers.isolate(repo, work/'repo', function)
    ws = private_repo/'nonmatchings'/function
    name = 'shift_helper_replay'
    started = time.perf_counter()
    att = workspace.score(ws, private_repo, name, source, conn=conn, func=function,
                          parent_attempt_id=parent_id, strategy='shift-helper-environment-replay',
                          relation='environment-revalidation', model='zero-model')
    compile_seconds = time.perf_counter()-started
    print('compiled', att.compiled, (att.frontend or {}).get('passed'), flush=True)
    if not att.compiled or (att.frontend or {}).get('passed') is not True:
        raise ValueError('snapshot candidate no longer passes compiler/frontend')

target = workspace.semantic_assembly((ws/'target_object_dump_normalized.s').read_text(), ws/'target.o')
candidate = workspace.semantic_assembly((ws/(name+'_object_dump_normalized.s')).read_text(), ws/(name+'.o'))
target_calls = project_headers.called_functions(target)
candidate_calls = project_headers.called_functions(candidate)
admission = []
for names in (target_calls, sorted(set(target_calls)|set(c.COMPILER_WORD_PAIR_HELPERS))) * 4:
    started = time.perf_counter()
    env, reports = c.load_binary_leaves(private_repo, names)
    admission.append({'names': names, 'seconds': time.perf_counter()-started, 'reports': reports,
                      'admitted': sorted(env.leaves)})
started = time.perf_counter()
panel = Panel(private_repo, ws, function, max_cases=64, max_steps=10000, exploration_cases=5000,
              header_source=source)
panel_seconds = time.perf_counter()-started
print('panel built', panel_seconds, 'cases',len(panel.cases), flush=True)

# Identical target cases, budgets and arities for preexisting target calls.
# Only candidate-only fixed helper admissions differ between paired runs.
extras = set(c.COMPILER_WORD_PAIR_HELPERS)-set(target_calls)
legacy_env = c.Environment({n:v for n,v in panel.callee_environment.leaves.items() if n not in extras},
                           dict(panel.callee_environment.outputs), panel.callee_environment.callbacks)
legacy_arities = {n:v for n,v in panel.arities.items() if n not in extras}
paired = {}
for label, arities, env in [('target_only_closure', legacy_arities, legacy_env),
                            ('fixed_helper_closure', panel.arities, panel.callee_environment)]:
    started = time.perf_counter()
    rows = d.run_suite(panel.target, candidate, panel.cases, target_name=function,
        candidate_name=function+'-candidate', call_arities=arities,
        return_registers=panel.returns, max_steps=panel.max_steps, callee_environment=env)
    paired[label] = {'seconds':time.perf_counter()-started,
        'counts':dict(Counter(row.status for row in rows)),
        'target_status':dict(Counter(row.target.status for row in rows)),
        'candidate_status':dict(Counter(row.candidate.status for row in rows)),
        'candidate_errors':dict(Counter(row.candidate.error for row in rows if row.candidate.error)),
        'rows':[row.to_dict() for row in rows]}
    print(label, paired[label]['counts'], paired[label]['candidate_errors'], flush=True)

before, after = paired['target_only_closure'], paired['fixed_helper_closure']
target_same = [r['target'] for r in before['rows']] == [r['target'] for r in after['rows']]
report = {'function': function, 'source_snapshot':str(original), 'parent_attempt_id':parent_id,
          'runtime_sha256': {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                            for module in (c,d)},
          'source_sha256':source_hash, 'prior_score':prior_score, 'workspace':str(ws),
          'attempt':asdict(att), 'compile_seconds':compile_seconds,
          'target_calls':target_calls, 'candidate_calls':candidate_calls,
          'admission_timings':admission, 'panel_seconds':panel_seconds,
          'panel':panel.report, 'paired':paired, 'identical_target_runs':target_same,
          'authority':'same-source same-case diagnostic environment replay; no source repair or exactness promotion'}
suffix = '' if function == 'calculateFixedAngleFromDeltaXZ' else '-'+function
(out/('shift-helper-replay'+suffix+'.json')).write_text(json.dumps(report,indent=2))
print(json.dumps({'function':function,'parent':parent_id,'target_calls':target_calls,
                  'candidate_calls':candidate_calls,'admission_timings':[r['seconds'] for r in admission],
                  'panel_seconds':panel_seconds,'identical_target_runs':target_same,
                  'counts':{k:v['counts'] for k,v in paired.items()}},indent=2))
