"""Bounded read-only display of automatic captured-entry comparisons."""
from eval.progress_integration import artifact_rows, binding_current


def project(runtime, bindings, run, commit):
    latest = (runtime or {}).get('latest') or {}
    if not latest:
        return {'status': 'not_run', 'commit': commit, 'capture_count': 0,
                'counts': {}, 'artifacts': [], 'issues': [],
                'current_binding': False, 'current_pass': False}
    name = latest.get('function')
    expected = latest.get('source_binding') or {}
    matches = binding_current(expected, bindings.get(name))
    artifacts, issues = artifact_rows(latest.get('artifacts') or [], run,
                                     '/api/runtime-artifact')
    issues = [issue.replace('integration artifact', 'capture artifact') for issue in issues]
    if not matches:
        issues.append('Current source, attempt, or certificate differs from the captured comparison')
    counts = {key: value if type(value) is int and value >= 0 else 0
              for key, value in ((key, (latest.get('counts') or {}).get(key, 0))
                                 for key in ('passed', 'failed', 'inconclusive'))}
    count = latest.get('capture_count', 0)
    count = count if type(count) is int and count >= 0 else 0
    status = latest.get('status', 'unavailable')
    if status == 'passed':
        if not any(row['kind'] == 'receipt' and row['available'] for row in artifacts):
            issues.append('No hash-checked capture/replay receipt is available')
        if not count or not counts['passed'] or counts['failed'] or counts['inconclusive']:
            issues.append('Recorded comparison counts do not establish a passing captured entry')
    return {'status': status, 'function': name, 'plan_id': latest.get('plan_id'),
            'checkpoint': latest.get('checkpoint'), 'commit': commit,
            'source_binding': expected, 'capture_count': count, 'counts': counts,
            'current_binding': matches, 'current_pass': status == 'passed' and not issues,
            'artifacts': artifacts, 'issues': issues,
            'error': str(latest.get('error') or '')[:2000],
            'started_at': latest.get('started_at'), 'updated_at': latest.get('updated_at'),
            'scope': 'Observed integer-leaf entries; not whole-game equivalence.',
            'authoritative': False}
