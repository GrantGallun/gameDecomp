"""Explicit drained-checkpoint amendment; never silently backfill old runs."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
STAGE = OUT / 'staged-code'
sys.path.insert(0, str(STAGE))
from eval import campaign_state, completion_campaign as campaign, frozen_wavefront, fast_campaign


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((OUT / 'staged-manifest.json').read_bytes())
    tests = (OUT / 'staged-tests.log').read_text()
    if not re.search(r'^\d+ passed in [\d.]+s\s*$', tests, re.M) or 'FAILED ' in tests or 'ERROR ' in tests:
        raise ValueError('staged tests did not pass')
    for rel, entry in manifest.items():
        if sha(STAGE / rel) != entry['new_sha256']:
            raise ValueError('staged file changed: ' + rel)
    with campaign.campaign_lock(RUN / 'campaign.lock'):
        service = json.loads((RUN / 'service.json').read_bytes())
        if service['status'] != 'paused' or service.get('worker_pid') or not (RUN / 'service.pause').exists():
            raise ValueError('campaign must be paused and drained')
        state = campaign_state.read(RUN / 'campaign.json')
        if state.get('inflight') or state.get('fast_inflight') or state.get('tu_index'):
            raise ValueError('unexpected inflight work or previous locality amendment')
        frozen_wavefront.verify_files(state['pins'])
        if frozen_wavefront.model_digest(state['config']['endpoint'], state['config']['model']) != state['model_digest']:
            raise ValueError('model changed')
        with sqlite3.connect(f'file:{RUN / "campaign.sqlite"}?mode=ro', uri=True) as conn:
            inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
            index = campaign.translation_units(conn)
        if campaign.digest(inventory) != state['inventory_sha256']:
            raise ValueError('inventory changed')
        index = {n: u for n, u in index.items() if n in state['nodes']}
        replay = json.loads((OUT / 'ordering-replay.json').read_bytes())
        if campaign.digest(index) != replay['index_sha256']:
            raise ValueError('index differs from tested replay')
        for rel, entry in manifest.items():
            live = RUN / 'code' / rel
            if (sha(live) if live.exists() else None) != entry['old_sha256']:
                raise ValueError('live file changed: ' + rel)
        revision = RUN / 'revisions/20260912-decomp-order'
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json', 'launch.json', 'service.json', 'service-control.json'):
            shutil.copy2(RUN / name, revision / name)
        campaign_state.atomic(revision / 'previous-state-store.json', {
            'store': str(RUN / 'campaign.state.sqlite'),
            'pointer': json.loads((RUN / 'campaign.json').read_bytes()),
            'restore': 'Previous compact pointer references immutable commits in original run store; '
                       'restore previous-code and this pointer together while paused.'})
        for rel in manifest:
            live = RUN / 'code' / rel
            if live.exists():
                backup = revision / 'previous-code' / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, backup)
        for rel in manifest:
            live = RUN / 'code' / rel
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(STAGE / rel, live)
        newpins = campaign._pins(RUN / 'code', Path(state['config']['repo']))
        newpins.update({n: h for n, h in state['pins'].items()
                        if Path(n).is_relative_to(Path(state['config']['repo']) / 'nonmatchings')})
        changed = {n for n in newpins.keys() | state['pins'].keys() if newpins.get(n) != state['pins'].get(n)}
        if changed != {str(RUN / 'code' / rel) for rel in ('miner/units.py', 'solver/repair_queue.py', 'eval/completion_campaign.py')}:
            raise ValueError('unexpected changed pins: ' + str(changed))
        state['tu_index'] = index
        state['tu_index_provenance'] = {
            'algorithm': 'function-range-clusters-v2', 'inventory_sha256': state['inventory_sha256'],
            'index_sha256': campaign.digest(index), 'role': 'ordering heuristic over recorded ELF ranges; '
            'no reference TU assignment or interface authority'}
        record = {'kind': 'decomp-order-amendment', 'applied_at': time.time(),
            'authorization': 'User asked to review and improve Claude file/decomp ordering; '
                             'prior authorization to apply improvements to current run persists.',
            'policy': 'callee-cluster-v2', 'changed_files': manifest, 'changed_pins': sorted(changed),
            'index': state['tu_index_provenance'], 'validation': str(OUT),
            'prior_metrics': dict(state.get('fast_metrics', {})), 'prior_summary': dict(state['summary']),
            'limits': 'Fair visit/lane bands precede callee depth. Cycles share depth. Parallel resource '
                      'dispatch may overlap caller and callee. No budgets, evidence keys or acceptance gates changed.'}
        state['pins'] = newpins
        state.setdefault('runtime_amendments', []).append(record)
        selected = fast_campaign.project(state)
        fast_campaign.summary(state, selected)
        assert state['summary'] == record['prior_summary']
        launch = json.loads((RUN / 'launch.json').read_bytes())
        launch['runtime_amendment'] = str(revision / 'amendment.json')
        launch['command'][launch['command'].index('--max-work-items') + 1] = '200'
        campaign_state.atomic(revision / 'amendment.json', record)
        frozen_wavefront.verify_files(newpins)
        campaign_state.Store(RUN / 'campaign.json').save(state)
        campaign_state.atomic(RUN / 'launch.json', launch)
        result = {'revision': str(revision), 'changed_pins': sorted(changed),
                  'summary': state['summary'], 'index_count': len(index), 'next': selected,
                  'checkpoint': json.loads((RUN / 'campaign.json').read_bytes())['commit']}
        campaign_state.atomic(OUT / 'deployment.json', result)
        print(json.dumps(result))


if __name__ == '__main__':
    main()
