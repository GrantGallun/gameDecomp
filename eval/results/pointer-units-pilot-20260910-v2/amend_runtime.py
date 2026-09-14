"""Apply a reviewed four-file pointer amendment only at an idle durable boundary."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import time

folder = Path(__file__).resolve().parent
root = folder.parents[2]
run = root/'eval/results/resume-pipeline-20260908'
sys.path.insert(0, str(run/'code'))
from eval import agentrepair, completion_campaign, frozen_wavefront

manifest = json.loads((folder/'runtime/manifest.json').read_text())
assert {Path(p).name for p in manifest} == {'address_units.py','compile_recovery.py','modelrepair.py','repair.py'}
locks = []
for name in ('resume-supervisor.lock', 'campaign.lock'):
    handle = (run/name).open('a+b')
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    locks.append(handle)
assert (run/'service.pause').exists()
assert json.loads((run/'service-control.json').read_text())['paused'] is True
state = json.loads((run/'campaign.json').read_text())
assert not state.get('inflight'), 'must finish in-flight work before amendment'
frozen_wavefront.verify_files(state['pins'])
revision = run/'revisions/20260910-pointer-call-units'
revision.mkdir(exist_ok=False)
agentrepair._atomic_json(revision/'campaign.json', state)
before = {}
after = {}
for name, row in manifest.items():
    path = Path(name)
    staged = Path(row['staged'])
    assert path.resolve().parent == (run/'code/solver').resolve()
    assert staged.resolve().is_relative_to((folder/'runtime/code').resolve())
    before[name] = path.read_bytes()
    after[name] = staged.read_bytes()
    assert hashlib.sha256(before[name]).hexdigest() == state['pins'][name] == row['before_sha256']
    assert hashlib.sha256(after[name]).hexdigest() == row['after_sha256']
    (revision/path.name).write_bytes(before[name])
record = {'kind':'pointer-repair-runtime-amendment', 'status':'prepared', 'time':time.time(),
    'reason':'User requested broad automatic pointer handling in the pipeline', 'files':manifest,
    'validation': {'main_tests_passed':211, 'staged_tests_passed':107,
        'isolated_candidates':46, 'frontend_passing_score_gains':14, 'object_exact_recoveries':3,
        'staged_smoke':str(folder/'runtime-smoke.json')},
    'scope':'shared target-call byte units in recovery, normalization and deterministic search; no model/prompt/budget changes; existing acceptance gates retained',
    'prior_checkpoint':str(revision/'campaign.json')}
agentrepair._atomic_json(revision/'amendment.json', record)
try:
    for name, data in after.items():
        path = Path(name)
        temporary = path.with_suffix('.pointer-update.tmp')
        temporary.write_bytes(data)
        os.replace(temporary, path)
    updated = dict(state['pins'])
    updated.update({name: row['after_sha256'] for name, row in manifest.items()})
    repo = Path(state['config']['repo'])
    actual = completion_campaign._pins(run/'code', repo)
    actual.update(frozen_wavefront.file_hashes([Path(p) for p in updated if Path(p).is_relative_to(repo/'nonmatchings')]))
    assert actual == updated, 'unexpected runtime or binary input change'
    state['pins'] = updated
    state.setdefault('runtime_amendments', []).append(str(revision/'amendment.json'))
    agentrepair._atomic_json(run/'campaign.json', state)
    record.update(status='applied', applied_at=time.time())
    agentrepair._atomic_json(revision/'amendment.json', record)
except BaseException:
    for name, data in before.items():
        Path(name).write_bytes(data)
    agentrepair._atomic_json(run/'campaign.json', json.loads((revision/'campaign.json').read_text()))
    record['status'] = 'rolled_back'
    agentrepair._atomic_json(revision/'amendment.json', record)
    raise
print(json.dumps({'status':record['status'], 'revision':str(revision), 'nodes_preserved':len(state['nodes'])}))
