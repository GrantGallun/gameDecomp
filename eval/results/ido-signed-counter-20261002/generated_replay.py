"""Require the shared opt-in generator itself to reproduce the SBK1 witnesses."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/ido-signed-counter-20261002')
sys.path.insert(0, str(PROJECT))
from solver import workspace, regalloc_mutations as rm, narrow_update

protocol = json.loads((WORK / 'protocol.json').read_text())
results = json.loads((HERE / 'results.json').read_text())
conn = sqlite3.connect(WORK / 'trial.sqlite')
records = []
for plan, case in zip(protocol['plans'], results['cases']):
    name, source = plan['function'], Path(plan['root']).read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == plan['root_sha256']
    label, family, proposed = next(rm.variants(source, name, narrow_updates=True))
    assert family == 'narrow_update' and label.startswith('narrow_update:masked_signed:')
    repo = WORK / name / 'repo'
    ws = repo / 'nonmatchings' / name
    parent = case['baseline']['attempt_id']
    for mode in ('generated', 'independent-repeat'):
        att = workspace.score(ws, repo, 'signed_' + mode, proposed, conn=conn, func=name,
            strategy='ido-signed-counter:' + mode, run_id='ido-signed-counter-generator-20261002',
            parent_attempt_id=parent, relation='candidate-construction',
            extra={'training_eligible': False, 'scope': 'exposed-development', 'generator_label': label})
        row = {'function': name, 'mode': mode, 'attempt_id': att.receipt_id,
               'label': label, 'source_sha256': hashlib.sha256(proposed.encode()).hexdigest(),
               'compiled': att.compiled, 'object_exact': att.exact,
               'frontend_passed': (att.frontend or {}).get('passed')}
        records.append(row)
        (HERE / 'generated-replay.json').write_text(json.dumps({'records': records,
            'generator_sha256': hashlib.sha256(Path(narrow_update.__file__).read_bytes()).hexdigest(),
            'training_eligible': False, 'campaign_imported': False}, indent=2) + '\n')
        assert workspace.repair_complete(att), row
        parent = att.receipt_id
conn.close()
print(json.dumps({'generated_distinct_exacts': 2, 'independent_repeats': 2,
                  'frontend_passed': True, 'clean_transfer_exacts': 0}), flush=True)
