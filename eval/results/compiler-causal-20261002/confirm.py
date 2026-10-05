"""Independently reproduce the control match and verify the retained ROM union."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
inputs = json.loads((HERE / 'inputs.json').read_text())
WORK = Path(inputs['work'])
sys.path.insert(0, inputs['code'])
from eval import integration_gate, prepare_integration
from solver import workspace


def main():
    start = time.monotonic()
    function = sys.argv[1] if len(sys.argv) > 1 else 'randomNextObject'
    item = next(x for x in inputs['inputs'] if x['function'] == function)
    folder = WORK / function
    repo = folder / 'repo'
    original = Path('/home/grant/decomp/sbk1')
    revision = sys.argv[3] if len(sys.argv) > 3 else ''
    prefix = function + ('-' + revision if revision else '')
    source_path = folder / ('integration-typed.c' if revision == 'typed' else 'materialization-exact.c')
    source = source_path.read_text()
    ws = workspace.bootstrap(repo, function)
    conn = sqlite3.connect(WORK / 'trial.sqlite')
    group = 'increment' if function == 'randomNextObject' else 'counter-reread'
    parent = json.loads((HERE / (function + '-' + group + '.json')).read_text())['rows'][-1]['attempt_id']
    if revision:
        parent = json.loads((HERE / (function + '-confirmation.json')).read_text())['repeats'][-1]['attempt_id']
    repeats = []
    for index in range(2):
        attempt = workspace.score(ws, repo, f'causal_confirmation_{revision}_{index}', source, conn=conn, func=function,
            strategy='compiler-causal:independent-confirmation', run_id='compiler-causal-20261002',
            parent_attempt_id=parent, relation='confirm',
            extra={'training_eligible': False, 'assistance': 'game headers and unknown earlier ancestry',
                   'scope': 'Independent repeat of a candidate-owned causal intervention',
                   'integration_revision': revision,
                   'destination_declaration_assistance': bool(revision)})
        conn.commit()
        assert workspace.repair_complete(attempt), 'control match did not reproduce'
        repeats.append({'attempt_id': attempt.receipt_id, 'object_exact': attempt.exact,
                        'frontend_passed': attempt.frontend['passed']})
    conn.close()
    path = prepare_integration.prepare(repo=original, db=WORK / 'trial.sqlite',
        entries=[{'function': function, 'source': str(source_path), 'attempt_id': attempt.receipt_id,
                  'verification': attempt.verification}], output_dir=WORK / (prefix + '-prepared'))
    previous = json.loads((HERE.parent / 'integration-headers-20261002/proof.json').read_text())
    manifests = [Path(sys.argv[2]) if len(sys.argv) > 2 else Path(previous['manifest']), path]
    combined = WORK / (prefix + '-combined')
    combined.mkdir(exist_ok=False)
    merged = None
    for manifest_path in manifests:
        data = json.loads(manifest_path.read_text())
        if merged is None:
            merged = {**data, 'replacements': [], 'lineage': []}
        assert all(merged[key] == data[key] for key in ('reference_rom', 'reference_sha256', 'built_rom'))
        for entry in data['replacements']:
            assert entry['path'] not in {r['path'] for r in merged['replacements']}, 'overlapping translation unit'
            name = f'{len(merged["replacements"]):03d}.c'
            content = (manifest_path.parent / entry['replacement']).read_bytes()
            assert hashlib.sha256(content).hexdigest() == entry['replacement_sha256']
            (combined / name).write_bytes(content)
            merged['replacements'].append({**entry, 'replacement': name})
        merged['lineage'].extend(data['lineage'])
    merged['source_manifests'] = [str(p) for p in manifests]
    merged['scope'] = 'Private combined gate; certificates checked against each source ledger; no campaign import'
    manifest = combined / 'manifest.json'
    manifest.write_text(json.dumps(merged, indent=2) + '\n')
    receipt_path = WORK / (prefix + '-integration.json')
    receipt = integration_gate.run(repo=original, manifest=manifest, output=receipt_path)
    result = {'function': function, 'repeats': repeats, 'whole_rom_verified': receipt['whole_rom_verified'],
              'status': receipt['status'], 'proposed_union_count': len(merged['lineage']),
              'verified_union_count': len(merged['lineage']) if receipt['whole_rom_verified'] else None,
              'manifest': str(manifest), 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
              'receipt': str(receipt_path), 'receipt_sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
              'seconds': time.monotonic() - start,
              'not_exact_at_input_freeze': item['research_exact_attempts_at_freeze'] == 0 and item['campaign_status_at_freeze'] not in ('object_exact', 'integrated'), 'imported': False,
              'training_eligible': False, 'integration_revision': revision,
              'destination_declaration_assistance': bool(revision)}
    (HERE / (prefix + '-confirmation.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    assert receipt['whole_rom_verified']


if __name__ == '__main__':
    main()
