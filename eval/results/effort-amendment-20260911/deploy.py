"""Install the reasoned-effort option at a drained, locked checkpoint.

Mirrors optimization-implementation-20260911/deploy.py: refuses unless paused and
drained, pins and model weights verify, and the live files still match the
staging base. Only the two staged files may change pins.
"""
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
STAGE = OUT / 'staged-code'
sys.path.insert(0, str(ROOT))
from eval import campaign_state, completion_campaign as campaign, frozen_wavefront  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((OUT / 'staged-manifest.json').read_bytes())
    for relative, entry in manifest.items():
        if sha(STAGE / relative) != entry['new_sha256']:
            raise ValueError('staged file changed after manifest: ' + relative)
    tests = (OUT / 'staged-tests.log').read_text()
    if not re.search(r'^\d+ passed in [\d.]+s\s*$', tests, re.M) or 'failed' in tests or 'error' in tests.lower():
        raise ValueError('staged release suite not passing')
    with campaign.campaign_lock(RUN / 'campaign.lock'):
        service = json.loads((RUN / 'service.json').read_bytes())
        if service['status'] != 'paused' or service.get('worker_pid') or not (RUN / 'service.pause').exists():
            raise ValueError('campaign must be paused and drained')
        state = campaign_state.read(RUN / 'campaign.json')
        if state.get('inflight') or state.get('fast_inflight'):
            raise ValueError('unsettled work')
        frozen_wavefront.verify_files(state['pins'])
        if frozen_wavefront.model_digest(state['config']['endpoint'], state['config']['model']) != state['model_digest']:
            raise ValueError('model weights changed')
        for relative, entry in manifest.items():
            live = RUN / 'code' / relative
            if (sha(live) if live.exists() else None) != entry['old_sha256']:
                raise ValueError('live file differs from staging base: ' + relative)
        if state['runtime_options'].get('reasoned_effort', 'profile') != 'profile':
            raise ValueError('reasoned effort already amended')
        revision = RUN / 'revisions/20260911-reasoned-effort-medium'
        revision.mkdir(exist_ok=False)
        for name in ('campaign.json', 'launch.json', 'service.json', 'service-control.json'):
            shutil.copy2(RUN / name, revision / name)
        campaign_state.atomic(revision / 'campaign-full.json', state)
        for relative in manifest:
            live = RUN / 'code' / relative
            if live.exists():
                backup = revision / 'previous-code' / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, backup)
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(STAGE / relative, live)
        newpins = campaign._pins(RUN / 'code', Path(state['config']['repo']))
        newpins.update({name: digest for name, digest in state['pins'].items()
                        if Path(name).is_relative_to(Path(state['config']['repo']) / 'nonmatchings')})
        changedpins = {name for name in newpins.keys() | state['pins'].keys()
                       if newpins.get(name) != state['pins'].get(name)}
        allowed = {str(RUN / 'code' / relative) for relative in manifest}
        if not changedpins <= allowed:
            raise ValueError('unexpected input change outside deployment: ' + str(changedpins - allowed))
        record = {
            'kind': 'reasoned-effort-amendment', 'applied_at': time.time(),
            'authorization': 'User authorized implementation and trials of the optimization audit findings; '
                             'Claude Code completed the deployment Codex had staged when its usage ran out',
            'changed_files': manifest,
            'previous_metrics': dict(state.get('fast_metrics', {})),
            'validation': {'staged_tests': str(OUT / 'staged-tests.log'),
                           'evidence': str(ROOT / 'eval/results/optimization-audit-20260911/INFERENCE_RESULTS.md')},
            'runtime_options': {**state['runtime_options'], 'reasoned_effort': 'medium'},
            'change': 'reasoned_alternative jobs requesting high effort run at medium; all other profiles unchanged',
            'evidence_summary': '12-prompt paired replay: high 11/12 incomplete, 1 compile+frontend, 0 exact; '
                                'medium 4/12 incomplete, 4 compile+frontend, 1 exact (initFixedTransform); '
                                'medium 520s vs high 632s wall. Small sample; bounded development trial.',
            'acceptance': 'Compiler, frontend, source bindings, semantic and exactness gates unchanged; '
                          'consumed profile budgets are not replayed'}
        state['pins'] = newpins
        state['runtime_options'] = record['runtime_options']
        state.setdefault('runtime_amendments', []).append(record)
        launch = json.loads((RUN / 'launch.json').read_bytes())
        command = launch['command']
        if '--reasoned-effort' in command:
            command[command.index('--reasoned-effort') + 1] = 'medium'
        else:
            command.extend(['--reasoned-effort', 'medium'])
        launch['runtime_amendment'] = str(revision / 'amendment.json')
        campaign_state.atomic(revision / 'amendment.json', record)
        campaign_state.Store(RUN / 'campaign.json').save(state)
        campaign_state.atomic(RUN / 'launch.json', launch)
        frozen_wavefront.verify_files(newpins)
        print(json.dumps({'revision': str(revision), 'changed_pins': sorted(changedpins),
                          'summary': state['summary'], 'runtime_options': state['runtime_options'],
                          'command_tail': command[-6:]}), flush=True)


if __name__ == '__main__':
    main()
