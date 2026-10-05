"""Scoped active-header object-conflict amendment; preserves all retained nodes.
Use stage.py, verify_stage.py, then apply_amendment.py --apply after a drained pause.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

import stage

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
LAUNCH = CONTROL / 'launch.json'
KIND = '20261004-compiler-localization'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name('.' + path.name + '.rollback')
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def validate_payload(manifest: dict, staged_root: Path) -> None:
    if manifest.get('kind') != 'unapplied-compiler-localization-stage' or manifest.get('dry') is not False:
        raise ValueError('not an applicable (non-dry) integration-headers stage manifest')
    changed = manifest.get('changed')
    if not isinstance(changed, dict) or set(changed) != set(stage.CHANGED):
        raise ValueError('changed-file map differs from the reviewed scope')
    for rel, row in changed.items():
        if Path(rel).is_absolute() or '..' in Path(rel).parts:
            raise ValueError(f'unsafe staged path: {rel}')
        if (not isinstance(row, dict) or not isinstance(row.get('new_sha256'), str)
                or ((rel in stage.NEW) != (row.get('old_sha256') is None))):
            raise ValueError(f'invalid changed-file row: {rel}')
        file = staged_root / rel
        if not file.is_file() or sha(file) != row['new_sha256']:
            raise ValueError(f'staged file hash mismatch: {rel}')
    if manifest.get('inflight_at_stage'):
        raise ValueError('staged while work was in flight')


def fires_ok() -> bool:
    proof_path = HERE.parents[2] / 'campaign-localization-20261004/fire-staged.json'
    try:
        proof = json.loads(proof_path.read_bytes())
        manifest = json.loads((HERE / 'stage.json').read_bytes())
        return (proof.get('manifest_sha256') == sha(HERE / 'stage.json')
            and proof['root_compiled'] and proof['attribution_status'] == 'verified'
            and proof['packet']['status'] == 'measured' and proof['probe_count'] > 0
            and proof['prompt_has_localization'] and proof['all_probes_parented']
            and proof.get('search_prompt_has_localization') is True
            and proof['payload_hashes'] == {rel: row['new_sha256'] for rel, row in manifest['changed'].items()})
    except (OSError, ValueError, KeyError):
        return False


def lock(path: Path):
    handle = path.open('a+b')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f'lock held: {path}') from exc
    return handle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--apply', action='store_true')
    actions.add_argument('--validate', action='store_true')
    args = parser.parse_args()
    manifest_bytes = (HERE / 'stage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    validate_payload(manifest, HERE / 'staged')
    if args.validate:
        print(json.dumps({'changed': len(manifest['changed'])}, indent=2))
        return
    if not args.apply:
        raise SystemExit('unapplied by default; review stage.json and stage-test.json, then pass --apply')
    tests = json.loads((HERE / 'stage-test.json').read_bytes())
    if (not tests.get('passed') or tests.get('dry') is not False
            or tests.get('manifest_sha256') != hashlib.sha256(manifest_bytes).hexdigest()
            or not set(stage.TESTS + stage.FROZEN_TESTS).issubset(tests.get('command', []))):
        raise RuntimeError('staged tests did not pass for this exact, non-dry manifest')
    if not fires_ok():
        raise RuntimeError('fresh staged localization integration proof missing or failing')
    if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
        raise RuntimeError('both durable pause markers required')
    if manifest['state_path'] != str(STATE) or manifest['frozen_project'] != str(FROZEN):
        raise RuntimeError('manifest targets another campaign')
    handles = [lock(CONTROL / 'resume-supervisor.lock'), lock(STATE.with_suffix('.lock'))]
    try:
        sys.path.insert(0, str(FROZEN))
        from eval import campaign_state, completion_campaign  # noqa: E402
        pointer_before = STATE.read_bytes()
        launch_before = LAUNCH.read_bytes()
        pointer = json.loads(pointer_before)
        state = campaign_state.read(STATE)
        if (pointer['commit'] != manifest['source_commit'] or
                hashlib.sha256(pointer_before).hexdigest() != manifest['source_pointer_sha256'] or
                hashlib.sha256(launch_before).hexdigest() != manifest['source_launch_sha256']):
            raise RuntimeError('checkpoint/launch moved since staging')
        if state.get('fast_inflight') or state.get('inflight'):
            raise RuntimeError('campaign has in-flight work')
        changed = manifest['changed']
        new_code_pins = {}
        for rel, row in changed.items():
            target = FROZEN / rel
            if (sha(target) if target.is_file() else None) != row['old_sha256']:
                raise RuntimeError(f'old frozen file drift: {rel}')
            if sha(HERE / 'staged' / rel) != row['new_sha256']:
                raise RuntimeError(f'staged file drift: {rel}')
            new_code_pins[str(target)] = row['new_sha256']
        unchanged = [p for p in state['pins'] if p not in new_code_pins]
        other = [p for p, digest in state['pins'].items()
                 if p not in new_code_pins and (sha(Path(p)) if Path(p).is_file() else None) != digest]
        if other:
            raise RuntimeError(f'{len(other)} unchanged pin mismatches, e.g. {other[:3]}')
        with sqlite3.connect(f'file:{NATIVE / "campaign.sqlite"}?mode=ro', uri=True) as conn:
            if conn.execute('PRAGMA quick_check').fetchone() != ('ok',):
                raise RuntimeError('campaign DB integrity check failed')
            inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
        if completion_campaign.digest(inventory) != manifest['inventory_sha256']:
            raise RuntimeError('inventory pin differs')
        if state['model_digest'] != manifest['model_digest']:
            raise RuntimeError('model pin differs')
        if completion_campaign.digest(state['nodes']) != manifest['baseline_nodes_sha256']:
            raise RuntimeError('retained nodes changed since staging')
        before_nodes = {name: {key: node.get(key) for key in (
            'status', 'source_sha256', 'attempt_id', 'score', 'verification')}
            for name, node in state['nodes'].items()}
        before_exact = pointer['summary']['object_exact_or_integrated']
        if before_exact != manifest['baseline_exact']:
            raise RuntimeError('baseline exact count differs from staged checkpoint')

        (HERE / 'campaign.before.json').write_bytes(pointer_before)
        (HERE / 'launch.before.json').write_bytes(launch_before)
        for rel, row in changed.items():
            if row['old_sha256'] is not None:
                archived = HERE / 'previous-code' / rel
                archived.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(FROZEN / rel, archived)
                if sha(archived) != row['old_sha256']:
                    raise RuntimeError(f'old code backup differs: {rel}')
        (HERE / 'before-hashes.json').write_text(json.dumps({
            'pointer_sha256': manifest['source_pointer_sha256'],
            'launch_sha256': manifest['source_launch_sha256'],
            'old_code': {rel: row['old_sha256'] for rel, row in changed.items()},
            'unchanged_pins_verified': len(unchanged)}, indent=2) + '\n')

        def restore() -> None:
            for rel, row in changed.items():
                target = FROZEN / rel
                if row['old_sha256'] is None:
                    target.unlink(missing_ok=True)
                else:
                    shutil.copy2(HERE / 'previous-code' / rel, target)
            atomic_bytes(STATE, pointer_before)
            atomic_bytes(LAUNCH, launch_before)

        try:
            for rel in changed:
                target = FROZEN / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(HERE / 'staged' / rel, target)
            if any(sha(FROZEN / rel) != row['new_sha256'] for rel, row in changed.items()):
                raise RuntimeError('installed code differs from staged code')
            state['pins'].update(new_code_pins)
            amendment = {
                'kind': KIND, 'applied_at': time.time(), 'revision': str(HERE),
                'source_checkpoint': pointer['commit'], 'changed': changed,
                'new_drift_imports': manifest['new_drift_imports'],
                'unchanged_pins_verified': len(unchanged),
                'inventory_sha256': manifest['inventory_sha256'], 'model_digest': manifest['model_digest'],
                'stage_test_sha256': sha(HERE / 'stage-test.json'),
                'evidence': ['eval/results/campaign-localization-20261004/fire-staged.json'],
                'limits': ('Candidate-only compiler attribution; additional byte-lane profile; ordinary repairs and object certificate authority retained. Original source coordinates and measured insertion ties are preserved. All probes are logged; diagnostic stores cannot become repair children. This installation imports no candidate, attempt or node and does not establish a new match.')}
            state.setdefault('runtime_amendments', []).append(amendment)
            campaign_state.Store(STATE).save(state)
            launch = json.loads(launch_before)
            launch.setdefault('code_hashes', {}).update(new_code_pins)
            campaign_state.atomic(LAUNCH, launch)
            importlib.reload(completion_campaign)
            actual_pins = completion_campaign._pins(FROZEN, Path(state['config']['repo']))
            actual_pins.update(completion_campaign.frozen_wavefront.file_hashes([
                Path(p) for p in state['pins']
                if Path(p).is_relative_to(Path(state['config']['repo']) / 'nonmatchings')]))
            if actual_pins != state['pins']:
                raise RuntimeError('installed code/input pin set differs from checkpoint')
            restored = campaign_state.read(STATE)
            after_nodes = {name: {key: node.get(key) for key in (
                'status', 'source_sha256', 'attempt_id', 'score', 'verification')}
                for name, node in restored['nodes'].items()}
            if (restored['pins'] != state['pins'] or before_nodes != after_nodes or
                    completion_campaign.digest(restored['nodes']) != manifest['baseline_nodes_sha256']):
                raise RuntimeError('checkpoint round-trip or retained node changed')
            after = json.loads(STATE.read_bytes())
            if after['summary']['object_exact_or_integrated'] != before_exact:
                raise RuntimeError('match count changed during machinery-only amendment')
        except BaseException as exc:
            restore()
            (HERE / 'rollback.json').write_text(json.dumps({
                'restored_pointer_sha256': sha(STATE), 'restored_launch_sha256': sha(LAUNCH),
                'error': f'{type(exc).__name__}: {exc}'}, indent=2) + '\n')
            raise
        amendment['result'] = {'commit': after['commit'], 'pointer_sha256': sha(STATE),
                               'launch_sha256': sha(LAUNCH),
                               'object_exact_or_integrated': after['summary']['object_exact_or_integrated']}
        (HERE / 'amendment.json').write_text(json.dumps(amendment, indent=2) + '\n')
        print(json.dumps(amendment['result'], indent=2))
    finally:
        for handle in reversed(handles):
            handle.close()


if __name__ == '__main__':
    main()
