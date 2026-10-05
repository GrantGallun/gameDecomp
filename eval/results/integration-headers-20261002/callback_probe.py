"""Isolated callback candidate probe; no controller import or live KB mutation."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
CODE = Path('/home/grant/decomp/experiments/integration-headers-20261002-v4/code')
WORK = Path('/home/grant/decomp/experiments/integration-callback-20261002-v2')
REPO = Path('/home/grant/decomp/sbk1')
sys.path.insert(0, str(CODE))
from eval import campaign_workers, integration_gate, prepare_integration
from solver import workspace


def main():
    WORK.mkdir(parents=True, exist_ok=False)
    function = 'addEndingActorShadowRenderCallback'
    inputs = json.loads((HERE / 'inputs.json').read_text())
    source = (HERE / (function + '.candidate.c')).read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == inputs['entries'][function]['source_sha256']
    assert 'void drawEndingActorShadow(void *);' in source
    needle = 'addRenderCallback(&gModelRenderCallbackList, drawEndingActorShadow, arg0)'
    assert source.count(needle) == 1
    candidate = source.replace(needle, 'addRenderCallback(&gModelRenderCallbackList, (RenderCallback)drawEndingActorShadow, arg0)')
    private_repo = campaign_workers.isolate(REPO, WORK / 'repo', function)
    ws = workspace.bootstrap(private_repo, function)
    trial = WORK / 'trial.sqlite'
    conn = sqlite3.connect(trial)
    conn.executescript((CODE / 'kb/schema.sql').read_text())
    campaign = Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite')
    conn.execute('ATTACH DATABASE ? AS origin', (campaign.as_uri() + '?mode=ro',))
    for table in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM origin.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE origin')
    rows, selected = [], None
    for label, code in [('baseline', source), ('candidate-own-callback-view', candidate)]:
        attempt = workspace.score(ws, private_repo, 'callback_' + label, code, conn=conn, func=function,
            strategy='integration-callback:' + label, run_id='integration-callback-20261002-v2',
            parent_attempt_id=selected.receipt_id if selected else None, relation='callback-type-view',
            extra={'training_eligible': False, 'origin_campaign_attempt': inputs['entries'][function]['attempt_id'],
                   'origin_source_sha256': inputs['entries'][function]['source_sha256'],
                   'assistance': 'retained-source with unknown earlier lineage and game headers'})
        conn.commit()
        rows.append({'label': label, 'attempt_id': attempt.receipt_id, 'compiled': attempt.compiled,
                     'frontend_passed': (attempt.frontend or {}).get('passed'),
                     'function_exact': (attempt.verification or {}).get('function_boundary', {}).get('function_exact'),
                     'object_exact': attempt.exact})
        if label == 'baseline':
            selected = attempt
        else:
            selected = attempt
    (HERE / 'callback-attempts.json').write_text(json.dumps(rows, indent=2) + '\n')
    print(json.dumps(rows, indent=2), flush=True)
    boundary_exact = (selected.verification or {}).get('function_boundary', {}).get('function_exact') is True
    if not selected.compiled or not (selected.exact or boundary_exact) or not (selected.frontend or {}).get('passed'):
        raise SystemExit('callback view did not retain certified matching bytes')
    path = WORK / 'candidate.c'
    path.write_text(candidate)
    entry = {'function': function, 'source': str(path), 'attempt_id': selected.receipt_id,
             'verification': selected.verification}
    callback_manifest = prepare_integration.prepare(repo=REPO, db=trial, entries=[entry],
                                                    output_dir=WORK / 'callback-prepared')
    global_proof = json.loads((HERE / 'proof.json').read_text())
    manifests = [Path(global_proof['manifest']), callback_manifest]
    merged = None
    combined = WORK / 'combined'
    combined.mkdir()
    for manifest_path in manifests:
        current = json.loads(manifest_path.read_text())
        if merged is None:
            merged = {**current, 'replacements': [], 'lineage': []}
        assert all(merged[k] == current[k] for k in ('reference_rom', 'reference_sha256', 'built_rom'))
        for replacement in current['replacements']:
            assert replacement['path'] not in {r['path'] for r in merged['replacements']}, 'overlapping TU requires combined preparation'
            filename = f'{len(merged["replacements"]):03d}.c'
            payload = (manifest_path.parent / replacement['replacement']).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == replacement['replacement_sha256']
            (combined / filename).write_bytes(payload)
            merged['replacements'].append({**replacement, 'replacement': filename})
        merged['lineage'].extend(current['lineage'])
    merged['source_manifests'] = [str(p) for p in manifests]
    merged['scope'] = 'Private combined ROM verification; per-entry source-bound certificates checked against respective ledgers; no campaign import'
    manifest = combined / 'manifest.json'
    manifest.write_text(json.dumps(merged, indent=2) + '\n')
    receipt = integration_gate.run(repo=REPO, manifest=manifest, output=WORK / 'integration.json')
    result = {'status': receipt['status'], 'whole_rom_verified': receipt['whole_rom_verified'],
              'functions_in_combined_union': len(merged['lineage']), 'manifest': str(manifest),
              'receipt': str(WORK / 'integration.json'), 'attempts': rows,
              'training_eligible': False, 'imported_into_campaign': False,
              'scope': 'Header-assisted hand-authored candidate view; not a generalized generator or clean discovery'}
    (HERE / 'callback-proof.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not receipt['whole_rom_verified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
