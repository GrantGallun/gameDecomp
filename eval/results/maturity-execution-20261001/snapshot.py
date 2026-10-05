"""Read immutable campaign receipts without hydrating all candidate histories."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import zlib

ROOT = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
CONTROL = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908')
OUT = Path(__file__).resolve().parent


def main():
    pointer = json.loads((ROOT / 'campaign.json').read_bytes())
    with sqlite3.connect((ROOT / pointer['store']).as_uri() + '?mode=ro', uri=True) as conn:
        manifest_bytes = conn.execute('SELECT manifest FROM commits WHERE id=?',
                                      (pointer['commit'],)).fetchone()[0]
        assert hashlib.sha256(manifest_bytes).hexdigest() == pointer['sha256']
        manifest = json.loads(manifest_bytes)
        compressed = conn.execute('SELECT payload FROM objects WHERE hash=?',
                                  (manifest['metadata'],)).fetchone()[0]
        raw = zlib.decompress(compressed)
        assert hashlib.sha256(raw).hexdigest() == manifest['metadata']
        state = json.loads(raw)
    service = json.loads((CONTROL / 'service.json').read_bytes())
    sweep = state.get('integration_sweep', {})
    latest = sweep.get('latest', {})
    records = latest.get('records', [])
    folders = list((ROOT / 'campaign-artifacts').glob('*-integration-sweep'))
    live_receipts = []
    if folders:
        folder = max(folders, key=lambda p: p.stat().st_mtime)
        for p in sorted(folder.glob('*-integration.json')):
            try:
                receipt = json.loads(p.read_bytes())
            except (OSError, ValueError):
                continue
            manifest_path = p.parent / (p.stem.removesuffix('-integration') + '-prepared') / 'manifest.json'
            receipt_functions, errors = [], []
            if manifest_path.exists():
                manifest_raw = manifest_path.read_bytes()
                assert hashlib.sha256(manifest_raw).hexdigest() == receipt['manifest_sha256']
                receipt_functions = [r['function'] for r in json.loads(manifest_raw).get('lineage', [])]
            build_log = Path(receipt['build_log']) if receipt.get('build_log') else None
            if receipt['status'] == 'build_failed' and build_log and build_log.is_relative_to(ROOT):
                log = re.sub(r'\x1b\[[0-9;]*m', '', build_log.read_text(errors='replace'))
                errors = [line.strip() for line in log.splitlines()
                          if re.search(r'error:|undefined reference|Error:', line)][:3]
            live_receipts.append({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                  'functions': receipt_functions, 'build_errors': errors,
                                  **{k: receipt.get(k) for k in ('status',
                                                               'whole_rom_verified', 'error')}})
    result = {
        'checkpoint': pointer['commit'], 'status': pointer.get('status'),
        'summary': pointer.get('summary'),
        'service': {k: service.get(k) for k in ('status', 'pid', 'worker_pid',
                    'completed_batches', 'consecutive_failures', 'last_returncode', 'error')},
        'pause_markers': [(str(p), p.exists()) for p in
                          (ROOT / 'service.pause', CONTROL / 'service.pause')],
        'inflight': [{k: job.get(k) for k in ('id', 'function', 'profile')}
                     for job in state.get('fast_inflight', [])],
        'integration': {k: latest.get(k) for k in ('status', 'checkpoint', 'selected',
                    'prior_union', 'verified_union', 'error')},
        'integration_records': [{k: r.get(k) for k in ('functions', 'status', 'reason', 'error')}
                                for r in records],
        'verified_union_count': len(sweep.get('verified_union', [])),
        'live_integration_receipts': live_receipts,
        'repair_yield': state.get('fast_metrics', {}).get('repair_yield'),
        'performance': {k: state.get('fast_metrics', {}).get(k) for k in
                        ('completed_items', 'improved_items', 'exact_items', 'session_pid')},
    }
    (OUT / 'latest-snapshot.json').write_text(json.dumps(result, indent=2) + '\n')
    report = {k: result[k] for k in ('checkpoint', 'status', 'summary', 'inflight',
                                    'verified_union_count', 'performance')}
    report['service'] = {k: v for k, v in result['service'].items() if k != 'error'}
    report['integration'] = {k: latest.get(k) for k in ('status', 'checkpoint', 'selected', 'error')}
    report['integration_record_count'] = len(records)
    report['live_integration_receipt_statuses'] = [r['status'] for r in live_receipts]
    report['repair_yield_totals'] = (result['repair_yield'] or {}).get('totals', {})
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
