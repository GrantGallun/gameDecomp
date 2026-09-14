"""Read-only verification of current checkpoint's latest whole-ROM evidence."""
import hashlib
import json
from pathlib import Path
import time
from eval import campaign_integration as integration, campaign_state

root = Path('/mnt/c/Code/gameDecomp')
run = root/'eval/results/resume-pipeline-20260908'
out = Path(__file__).resolve().parent/('rom-audit-'+str(time.time_ns())+'.json')
pointer = json.loads((run/'campaign.json').read_bytes())
state = campaign_state.read(run/'campaign.json')
history = state.get('integration_sweep', {})
latest = history.get('latest_success') or history.get('latest') or {}
names = latest.get('verified_union', [])
report = {'checkpoint': pointer['commit'], 'latest_integration_status': latest.get('status'),
          'integration_checkpoint': latest.get('checkpoint'), 'verified_union': names,
          'scope': 'read-only archived ROM/manifest/current-source verification; no new build or promotion',
          'artifacts': []}
try:
    assert latest.get('status') == 'rom_exact' and names
    bindings = latest['source_bindings']
    entries = [integration.entry(name, state['nodes'][name]) for name in names]
    for name in names:
        node = state['nodes'][name]
        assert integration.source_binding(node) == bindings[name]
        assert integration.file_digest(node['source']) == bindings[name]['source_sha256']
        assert node['status'] == 'integrated'
    integration.verify_union_receipt(latest['records'], entries, bindings, Path(state['config']['repo']))
    for item in latest.get('artifacts', []):
        path = run/item['path']
        assert path.resolve().is_relative_to(run.resolve())
        digest = integration.file_digest(path)
        report['artifacts'].append({**item, 'current_sha256': digest, 'hash_matches': digest == item['sha256']})
        assert digest == item['sha256']
    record = next(r for r in reversed(latest['records']) if r.get('status') == 'rom_exact'
                  and set(r.get('functions', [])) == set(names))
    receipt = json.loads(Path(record['receipt']).read_bytes())
    report.update(result='verified', receipt=record['receipt'], target_sha256=receipt['target_sha256'],
                  candidate_sha256=receipt['candidate_sha256'], rom_bytes=receipt['candidate_bytes'])
except Exception as exc:
    report.update(result='unavailable_or_stale', error=f'{type(exc).__name__}: {exc}')
out.write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps({'output': str(out), **report}, indent=2))
