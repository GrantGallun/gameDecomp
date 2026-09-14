"""Read-only, source-bound display of disposable integration sweep receipts."""
import hashlib
from pathlib import Path, PurePosixPath


def artifact_path(run, text):
    relative = PurePosixPath(str(text))
    if (relative.is_absolute() or not relative.parts or '..' in relative.parts
            or '\\' in str(text) or ':' in str(text)):
        raise ValueError('integration artifact must be run-relative')
    root = Path(run).resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError('integration artifact escapes run directory')
    return path


def read_artifact(run, row):
    if row.get('kind') not in {'receipt', 'log'}:
        raise ValueError('only integration receipts and logs are served')
    path = artifact_path(run, row.get('path', ''))
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('integration artifact exceeds dashboard read limit')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row.get('sha256'):
        raise ValueError('integration artifact checksum mismatch')
    return raw


def binding_current(expected, current):
    return bool(current and all(current.get(k) == expected.get(k)
        for k in ('source_sha256', 'attempt_id', 'verification_sha256'))
        and expected.get('source_sha256') and expected.get('verification_sha256'))


def artifact_rows(rows, run, route):
    """Project only recorded artifacts; hash receipts before presenting them."""
    artifacts, issues = [], []
    for row in rows:
        entry = {k: row.get(k) for k in ('path', 'sha256', 'kind')}
        entry['available'] = False
        if entry['kind'] == 'receipt':
            try:
                read_artifact(run, entry)
                entry['available'] = True
            except (OSError, ValueError) as exc:
                issues.append(str(exc))
        elif entry['kind'] == 'log':
            # Read/hash a potentially large log only when requested.
            try:
                entry['available'] = artifact_path(run, entry['path']).is_file()
            except (OSError, ValueError):
                pass
        if entry['available']:
            entry['url'] = route + '?sha256=' + str(entry['sha256'])
        artifacts.append(entry)
    return artifacts, issues


def project(sweep, bindings, run, commit):
    latest = (sweep or {}).get('latest') or {}
    if not latest:
        return {'status': 'not_run', 'current_binding': False, 'commit': commit,
                'functions': [], 'artifacts': [], 'issues': []}
    expected = latest.get('source_bindings') or {}
    selected = set(latest.get('selected') or [])
    prior = set(latest.get('prior_union') or [])
    union = set(latest.get('verified_union') or [])
    issues, functions = [], []
    for name, binding in sorted(expected.items()):
        current = bindings.get(name)
        matches = binding_current(binding, current)
        functions.append({'name': name, **{k: binding.get(k) for k in
            ('source_sha256', 'attempt_id', 'verification_sha256')},
            'current_binding': matches, 'new_selection': name in selected})
        if not matches and name in union:
            issues.append('Current source or certificate differs: ' + name)
    status = latest.get('status', 'unknown')
    if status == 'rom_exact' and (not union or not prior <= union <= selected | prior
            or not union <= set(expected) or union != set(sweep.get('verified_union') or [])):
        issues.append('Verified union does not match the bound cumulative candidate set')
    artifacts, artifact_issues = artifact_rows(latest.get('artifacts') or [],
        run, '/api/integration-artifact')
    issues.extend(artifact_issues)
    receipts = sum(row['kind'] == 'receipt' for row in artifacts)
    if status == 'rom_exact' and not receipts:
        issues.append('No source-bound receipt artifact is available')
    result = {'status': status, 'current_binding': status == 'rom_exact' and not issues,
            'commit': commit, 'checkpoint': latest.get('checkpoint'),
            'selected': sorted(selected), 'prior_union': sorted(prior),
            'verified_union': sorted(union), 'functions': functions,
            'unverified_selection': sorted(selected - union),
            'artifacts': artifacts, 'issues': issues,
            'scope': 'Disposable whole-ROM verification; canonical sources unchanged.',
            'complete_c_decompilation': False}
    if status != 'rom_exact' and (sweep or {}).get('latest_success'):
        result['previous_success'] = project({'latest': sweep['latest_success'],
            'verified_union': sweep.get('verified_union', [])}, bindings, run, commit)
    return result
