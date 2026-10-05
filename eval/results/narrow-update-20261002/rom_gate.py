"""Whole-ROM gate for the narrow-update generator's own winners (private; no campaign import)."""
from pathlib import Path
import hashlib, json, sqlite3, sys, time

HERE = Path(__file__).resolve().parent
WORK = Path('/home/grant/decomp/experiments/narrow-update-20261002')
bundle = json.loads((WORK / 'bundle.json').read_text())
sys.path.insert(0, bundle['code'])
from eval import integration_gate, prepare_integration
from solver import workspace

TYPED = [('void drawEndingCreditsIdleSparkle(void *);', 'void drawEndingCreditsIdleSparkle(EndingCreditsEffectActor *);'),
         ('void updateEndingCreditsIdleSparkle(void *arg0)', 'void updateEndingCreditsIdleSparkle(EndingCreditsEffectActor *arg0)'),
         ('&gMenuRenderCallbackList, drawEndingCreditsIdleSparkle, arg0', '&gMenuRenderCallbackList, (RenderCallback)drawEndingCreditsIdleSparkle, arg0')]


def main():
    start = time.monotonic()
    function, typed = sys.argv[1], len(sys.argv) > 2 and sys.argv[2] == 'typed'
    folder = WORK / function / 'narrow_update'
    source = (folder / 'winner.c').read_text()
    applied = []
    if typed:
        for before, after in TYPED:
            if source.count(before) == 1:
                source = source.replace(before, after); applied.append(before)
    tag = function + ('-typed' if typed else '')
    src_path = WORK / (tag + '-gate.c')
    src_path.write_text(source)
    original = Path('/home/grant/decomp/sbk1')
    ws = workspace.bootstrap(folder / 'repo', function)
    conn = sqlite3.connect(WORK / 'trial.sqlite')
    attempt = workspace.score(ws, folder / 'repo', f'gate_{tag}', source, conn=conn, func=function,
        strategy='narrow-update:rom-gate', run_id='narrow-update-20261002', parent_attempt_id=None, relation='confirm',
        extra={'training_eligible': False, 'assistance': 'game headers; destination type fixes' if typed else 'game headers'})
    conn.commit(); conn.close()
    assert workspace.repair_complete(attempt), 'object not exact'
    prepared = prepare_integration.prepare(repo=original, db=WORK / 'trial.sqlite',
        entries=[{'function': function, 'source': str(src_path), 'attempt_id': attempt.receipt_id, 'verification': attempt.verification}],
        output_dir=WORK / (tag + '-prepared'))
    previous = json.loads((HERE.parent / 'integration-headers-20261002/proof.json').read_text())
    manifests = [Path(previous['manifest']), prepared]
    combined = WORK / (tag + '-combined'); combined.mkdir(exist_ok=False)
    merged = None
    for mp in manifests:
        data = json.loads(mp.read_text())
        if merged is None:
            merged = {**data, 'replacements': [], 'lineage': []}
        assert all(merged[k] == data[k] for k in ('reference_rom', 'reference_sha256', 'built_rom'))
        for entry in data['replacements']:
            assert entry['path'] not in {r['path'] for r in merged['replacements']}, 'overlapping translation unit'
            name = f'{len(merged["replacements"]):03d}.c'
            content = (mp.parent / entry['replacement']).read_bytes()
            assert hashlib.sha256(content).hexdigest() == entry['replacement_sha256']
            (combined / name).write_bytes(content)
            merged['replacements'].append({**entry, 'replacement': name})
        merged['lineage'].extend(data['lineage'])
    merged['source_manifests'] = [str(p) for p in manifests]
    manifest = combined / 'manifest.json'
    manifest.write_text(json.dumps(merged, indent=2) + '\n')
    receipt = integration_gate.run(repo=original, manifest=manifest, output=WORK / (tag + '-integration.json'))
    result = {'function': function, 'typed_fixes_applied': applied, 'object_exact': attempt.exact,
              'whole_rom_verified': receipt['whole_rom_verified'], 'status': receipt['status'],
              'union_count': len(merged['lineage']), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
              'seconds': time.monotonic() - start, 'imported': False, 'training_eligible': False}
    (HERE / (tag + '-rom-gate.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))

main()
