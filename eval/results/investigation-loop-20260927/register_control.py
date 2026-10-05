"""Two preregistered source controls on the live agent's new near-match.

These are Codex-directed follow-ups, not additional autonomous gpt-oss successes.
Only the generated candidate, target assembly and compiler observations are used.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path('/home/grant/decomp/experiments/investigation-loop-20260927')
sys.path.insert(0, str(ROOT / 'v5/code'))
from eval import campaign_workers
from solver import workspace, compiler_experiment
from solver.experiment_memory import Notebook

name = '__osSiDeviceBusy'
original_repo = Path('/home/grant/decomp/sbk1')
repo = campaign_workers.isolate(original_repo, ROOT / 'register-control-repo', name)
ws = workspace.bootstrap(repo, name)
source = (ROOT / 'v5/model' / name / 'result.best.c').read_text()
parent = json.loads((ROOT / 'v5/model' / name / 'result.repair.json').read_text())['result']
if hashlib.sha256(source.encode()).hexdigest() != parent['best_source_sha256']:
    raise ValueError('near-match candidate changed')
variants = [
    ('plain_temporary', 'An ordinary scalar lifetime changes the loaded value allocation to a0.', 's32'),
    ('register_temporary', 'The register qualifier changes allocation without introducing a spill.', 'register s32'),
]
refine = '--refine' in sys.argv
if refine:
    variants = [
        ('register_only', 'Remove the array: the register local alone accounts for the target eight-byte frame.', 'register s32'),
        ('volatile_register', 'A volatile load needs a separate address temporary while retaining a0 for its value.', 'register s32'),
    ]
output = ROOT / ('register-control-refined' if refine else 'register-control')
output.mkdir(exist_ok=False)
memory = Notebook(output / 'experiments.jsonl', name, {'parent_source': parent['best_source_sha256'],
    'target_asm': hashlib.sha256(workspace.target_asm(ws, name).encode()).hexdigest(),
    'scope': 'codex-directed controlled follow-up, private development only'})
rows = []
with sqlite3.connect(ROOT / 'canary.sqlite') as conn:
    for label, hypothesis, declaration in variants:
        candidate = source.replace('    (void)dummy;', '    ' + declaration + ' status = SI_STATUS_REG;\n    (void)dummy;')
        candidate = candidate.replace('if (SI_STATUS_REG & 3)', 'if (status & 3)')
        if refine:
            candidate = candidate.replace('    int dummy[2]; // force stack frame\n', '').replace('    (void)dummy;\n', '')
        if label == 'volatile_register':
            candidate = candidate.replace('extern s32 SI_STATUS_REG;', 'extern volatile s32 SI_STATUS_REG;')
        workspace.assert_uncontaminated(candidate, repo, name)
        tag = name + '_control_' + label
        att = workspace.score(ws, repo, tag, candidate, conn=conn, func=name,
            parent_attempt_id=parent['best_attempt_id'], relation='controlled-register-experiment',
            action=hypothesis, strategy='codex-directed-investigation-control')
        (output / (label + '.c')).write_text(candidate)
        phase = compiler_experiment.inspect(repo, ws, name, candidate, output,
            hypothesis=hypothesis, object_path=ws / (tag + '.o') if att.compiled else None)
        row = {'label': label, 'hypothesis': hypothesis, 'score': att.score,
               'compiled': att.compiled, 'exact': workspace.repair_complete(att),
               'attempt_id': att.receipt_id, 'frontend': att.frontend, 'diff': att.diff,
               'compiler_stderr': att.compiler_stderr, 'phase': phase}
        memory.append({'action': label, 'hypothesis': hypothesis, 'status': 'measured',
            'parent_source_sha256': parent['best_source_sha256'],
            'child_source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
            'before': {'score': parent['best_residual']['weighted_progress_score']},
            'after': {'score': att.score, 'compiled': att.compiled, 'exact': workspace.repair_complete(att),
                      'diff': att.diff}, 'metadata': {'phase_status': phase['status'], 'comparable': phase['comparable']}})
        rows.append(row)
        (output / 'report.json').write_text(json.dumps(rows, indent=2) + '\n')
        print(json.dumps({k: row[k] for k in ('label', 'score', 'compiled', 'exact', 'diff')}), flush=True)
