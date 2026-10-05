"""Reconcile completed native receipts; never regenerate or overwrite solver attempts."""
from pathlib import Path
import hashlib
import json
import sqlite3

HERE = Path(__file__).resolve().parent
WORK = Path('/home/grant/decomp/experiments/narrow-update-20261002')
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    bundle = json.loads((WORK / 'bundle.json').read_text())
    for name, expected in bundle['code_pins'].items():
        assert sha(Path(bundle['code']) / name) == expected, name
    for root in bundle['roots']:
        assert sha(root['source']) == root['source_sha256']
    with sqlite3.connect((WORK / 'trial.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute('SELECT a.id,f.name AS function,a.source_code,a.source_sha256,a.compiled,a.exact,a.sampling,a.strategy FROM attempts a JOIN functions f ON f.addr=a.func_addr ORDER BY a.id')]
    certified = []
    for row in rows:
        assert hashlib.sha256(row['source_code'].encode()).hexdigest() == row['source_sha256']
        meta = json.loads(row['sampling'])
        assert meta['training_eligible'] is False
        if row['exact']:
            cert = meta['verification']
            assert cert['exact'] and cert['status'] == 'object_sections_exact'
            assert cert['candidate_source_sha256'] == row['source_sha256']
            assert meta['frontend']['passed']
            certified.append({'attempt_id': row['id'], 'function': row['function'],
                              'source_sha256': row['source_sha256'], 'strategy': row['strategy']})
    rom = []
    for tag in ('randomNextObject', 'updateEndingCreditsIdleSparkle', 'updateEndingCreditsIdleSparkle-typed'):
        path = WORK / (tag + '-integration.json')
        receipt = json.loads(path.read_text())
        summary = json.loads((HERE / (tag + '-rom-gate.json')).read_text())
        assert receipt['whole_rom_verified'] == summary['whole_rom_verified']
        assert receipt['status'] == summary['status']
        assert any(r['source_sha256'] == summary['source_sha256'] for r in certified)
        manifest = WORK / (tag + '-combined/manifest.json')
        data = json.loads(manifest.read_text())
        assert len(data['lineage']) == summary['union_count'] == 36
        for entry in data['replacements']:
            assert sha(manifest.parent / entry['replacement']) == entry['replacement_sha256']
        rom.append({'tag': tag, 'status': receipt['status'], 'whole_rom_verified': receipt['whole_rom_verified'],
                    'receipt': str(path), 'receipt_sha256': sha(path), 'manifest_sha256': sha(manifest),
                    'proposed_union_count': 36, 'verified_union_count': 36 if receipt['whole_rom_verified'] else None})
    audit = {'status': 'source_bound_native_receipts_verified', 'attempts': len(rows),
             'compile_failures': sum(not r['compiled'] for r in rows),
             'exact_certificate_receipts': certified,
             'certified_distinct_control_functions': len({r['function'] for r in certified}),
             'development_functions': sum(r['scope'] != 'exposed-motivating-control' for r in bundle['roots']),
             'rom_checks': rom, 'rom_union_scope': 'Two separate 35+1 unions; this follow-up did not test a combined 37-function union.',
             'new_clean_transfer_exacts': 0, 'training_eligible': False,
             'current_generator_matches_frozen': sha(HERE.parents[2] / 'solver/narrow_update.py') == bundle['code_pins']['solver/narrow_update.py']}
    (HERE / 'native-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    summary_path = HERE / 'verification.json'
    prior = HERE / 'verification-before-native-audit.json'
    if not prior.exists():
        prior.write_bytes(summary_path.read_bytes())
    summary = json.loads(summary_path.read_text())
    native = summary['native']
    native.update(new_generator_object_certificates=audit['certified_distinct_control_functions'],
                  exact_certificate_attempt_receipts=len(certified), native_attempts=len(rows),
                  native_compile_failures=audit['compile_failures'], freeze_complete=True,
                  frozen_control_functions=2, development_panel_selected=0,
                  development_panel_frozen=True, development_panel_run=False,
                  controls_run=True, new_clean_transfer_exacts=0,
                  note='Frozen comparison ran only the two exposed motivating controls. Both enabled arms matched on their first child; ordinary arms each exhausted 24 children. Seven source-bound exact certificate receipts cover two control functions. Three ROM checks produced two separate passing 36-function unions and one untyped declaration failure. Transfer and default activation remain unproved.',
                  audit_file='native-audit.json')
    summary_path.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: audit[k] for k in ('status','attempts','compile_failures','certified_distinct_control_functions','development_functions','current_generator_matches_frozen')}))


if __name__ == '__main__':
    main()
