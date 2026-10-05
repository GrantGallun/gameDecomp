"""Check that direct cc flags reproduce each ordinary scored object."""
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from solver import byte_certificate

HERE = ROOT / 'eval/results/direct-compiler-20260926'
OLD = Path('/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace/repo/nonmatchings/drawControllerPakFileDeleteConfirmOptions')
TAGS = {'baseline': 'drawControllerPakFileDeleteConfirmOptions',
        'prior_swap': 'drawControllerPakFileDeleteConfirmOptions_assignment_order_probe'}

def main():
    if (HERE / 'phase-gates.json').exists():
        raise RuntimeError('gate results already exist')
    records = []
    for phase in json.loads((HERE / 'phase-report-v2.json').read_text()):
        label = phase['label']
        output = Path(phase['assembly']).with_suffix('.o')
        command = [arg for arg in phase['command'] if arg != '-S'] + ['-c', '-o', str(output)]
        record = {'label': label, 'source_sha256': phase['source_sha256'],
                  'ordinary_db': phase['ordinary_db'], 'ordinary_receipt': phase['ordinary_receipt'],
                  'command': command, 'cwd': phase['cwd'], 'started': time.time()}
        log_path = output.parent / 'gate-invocations.jsonl'
        with log_path.open('a') as log:
            log.write(json.dumps(dict(record, status='started')) + '\n')
        try:
            run = subprocess.run(command, cwd=phase['cwd'], capture_output=True, text=True, timeout=120)
            record.update(returncode=run.returncode, stderr=run.stderr, stdout=run.stdout)
            if run.returncode == 0:
                record['certificate'] = byte_certificate.certify(OLD / (TAGS[label] + '.o'), output,
                    source=(HERE / (label + '.c')).read_text())
        except BaseException as exc:
            record['exception'] = repr(exc)
            raise
        finally:
            record['elapsed_seconds'] = time.time() - record['started']
            with log_path.open('a') as log:
                log.write(json.dumps(dict(record, status='finished')) + '\n')
        records.append(record)
    (HERE / 'phase-gates.json').write_text(json.dumps(records, indent=2) + '\n')
    print(json.dumps([{'label': r['label'], 'returncode': r.get('returncode'),
                      'exact': r.get('certificate', {}).get('exact'),
                      'status': r.get('certificate', {}).get('status'),
                      'error': r.get('certificate', {}).get('error')} for r in records], indent=2))
    assert all(r.get('certificate', {}).get('exact') is True for r in records)

if __name__ == '__main__':
    main()
