"""SBK1-only private compiler regression and real backend-stop receipt."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(PROJECT))
from solver import compiler_recipe, workspace

repo = Path('/home/grant/decomp/sbk1')
work = Path('/home/grant/decomp/experiments/compiler-sbk1-20261002/smoke2')
work.mkdir(parents=True, exist_ok=False)
conn = sqlite3.connect(work / 'trial.sqlite')
conn.executescript((PROJECT / 'kb/schema.sql').read_text())
original = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)
for name in ('randomNextObject',):
    function = original.execute('SELECT * FROM functions WHERE name=?', (name,)).fetchone()
    names = [r[1] for r in original.execute('PRAGMA table_info(functions)')]
    tu = original.execute('SELECT * FROM tus WHERE id=?', (function[names.index('tu_id')],)).fetchone()
    tu_names = [r[1] for r in original.execute('PRAGMA table_info(tus)')]
    conn.execute('INSERT INTO tus (' + ','.join(tu_names) + ') VALUES (' + ','.join('?' for _ in tu) + ')', tu)
    conn.execute('INSERT INTO functions (' + ','.join(names) + ') VALUES (' + ','.join('?' for _ in function) + ')', function)
row = original.execute("SELECT f.name,f.addr,t.name,t.object_path FROM functions f JOIN tus t ON t.id=f.tu_id "
                       "WHERE t.object_path='build/src/menu/race_setup/race_setup_menu.o' ORDER BY f.addr LIMIT 1").fetchone()
original.close()
conn.execute('INSERT INTO tus(id,name,object_path) VALUES(100000,?,?)', (row[2], row[3]))
conn.execute('INSERT INTO functions(addr,name,tu_id) VALUES(?,?,100000)', (row[1], row[0]))
conn.commit()

old = Path('/home/grant/decomp/experiments/narrow-update-20261002')
with sqlite3.connect('file:' + str(old / 'trial.sqlite') + '?mode=ro', uri=True) as controls:
    source = controls.execute('SELECT source_code FROM attempts WHERE id=27 AND exact=1').fetchone()[0]
ws = work / 'randomNextObject'
shutil.copytree(old / 'randomNextObject/narrow_update/repo/nonmatchings/randomNextObject', ws)
helper = (ws / 'build.sh').read_text()
marker = 'PROJECT_ROOT="$(cd "$SCRIPT_PATH/../.." && pwd)"'
assert helper.count(marker) == 1
(ws / 'build.sh').write_text(helper.replace(marker, f'PROJECT_ROOT="{repo}"'))
att = workspace.score(ws, repo, 'compiler_regression', source, conn=conn, func='randomNextObject',
                      strategy='sbk1-compiler-regression', run_id='compiler-sbk1-20261002',
                      extra={'training_eligible': False, 'scope': 'exposed-control-regression'})
assert att.compiled and att.exact and workspace.repair_complete(att)
assert att.verification['candidate_source_sha256'] == hashlib.sha256(source.encode()).hexdigest()

blocked = work / 'backend-stop'
blocked.mkdir()
shutil.copyfile(repo / 'tools/claude-decomp-env/build.sh', blocked / 'build.sh')
candidate = 'int ' + row[0] + '(void) { return 0; }\n'
try:
    workspace.score(blocked, repo, 'blocked', candidate, conn=conn, func=row[0],
                    strategy='sbk1-backend-stop', run_id='compiler-sbk1-20261002',
                    extra={'training_eligible': False})
except compiler_recipe.ObjectBackendRequired as exc:
    backend = exc.evidence
else:
    raise AssertionError('Expected real SBK1 backend requirement')
assert not (blocked / 'blocked.c').exists()
assert not (blocked / 'blocked.o').exists()
failed = conn.execute("SELECT id,source_sha256,compiled,exact,sampling FROM attempts "
                      "WHERE strategy='sbk1-backend-stop'").fetchall()
assert len(failed) == 1
assert failed[0][2:4] == (0, 0)
failure = json.loads(failed[0][4])['compiler_failure']
assert failure['compiler_invocations'] == 0 and failure['evidence'] == backend
assert failed[0][1] == hashlib.sha256(candidate.encode()).hexdigest()
report = {'game': 'sbk1', 'work': str(work), 'main_kb_modified': False,
          'campaign_imported': False, 'training_eligible': False,
          'control': {'function': 'randomNextObject', 'scope': 'exposed-control-regression',
                      'attempt_id': att.receipt_id, 'compiled': att.compiled, 'exact': att.exact,
                      'frontend_passed': att.frontend['passed'],
                      'certificate_status': att.verification['status'],
                      'source_sha256': hashlib.sha256(source.encode()).hexdigest()},
          'backend_failure': {'function': row[0], 'attempt_id': failed[0][0], **failure},
          'code_sha256': {str(p.relative_to(PROJECT)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (PROJECT / 'solver/workspace.py', PROJECT / 'solver/compiler_recipe.py')}}
(HERE / 'native-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
conn.close()
print(json.dumps({'control_exact': True, 'frontend_passed': True,
                  'backend_failure_logged': True, 'backend_compiler_invocations': 0}), flush=True)
