"""Install the reviewed frontend stage at the paused campaign boundary.

Default invocation is read-only validation. --apply explicitly updates twelve
frozen code files, their checkpoint pins and launch hashes. It does not resume
the controller or alter candidate, model, evidence, or attempt records.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

import stage

HERE = stage.HERE


def atomic_bytes(path: Path, payload: bytes) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f'.{path.name}.deployment-',
                                         delete=False) as stream:
            name = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


def archive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f'conflicting before-image: {path}')
        return
    with path.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def validate_payload(manifest: dict, staged_root: Path) -> None:
    if (manifest.get('kind') != 'unapplied-combined-frontier-frontend' or
            set(manifest.get('changed', {})) != set(stage.CHANGED) or
            set(manifest.get('tests', {})) != set(stage.TESTS) or
            manifest.get('frozen_test_sha256') != stage.sha(stage.FROZEN / stage.FROZEN_TEST)):
        raise ValueError('stage scope differs from reviewed twelve-file deployment')
    for rel, row in manifest['changed'].items():
        if (not isinstance(row, dict) or
                (rel in stage.NEW) != (row.get('old_sha256') is None) or
                not isinstance(row.get('new_sha256'), str) or
                (rel not in stage.NEW and not isinstance(row.get('old_sha256'), str)) or
                (row.get('old_launch_sha256') is not None and
                 not isinstance(row['old_launch_sha256'], str)) or
                (rel in stage.NEW and row.get('old_launch_sha256') is not None)):
            raise ValueError(f'invalid changed-file row: {rel}')
        if stage.sha(staged_root / rel) != row['new_sha256']:
            raise ValueError(f'staged source differs: {rel}')
    for roster in ('tests',):
        for rel, expected in manifest[roster].items():
            if stage.sha(staged_root / rel) != expected:
                raise ValueError(f'staged {roster} differs: {rel}')
    branch = stage.expected_branch()
    if (manifest.get('old_pin_digest') != branch['new_pin_digest'] or
            manifest.get('old_pin_count') != stage.EXPECTED_PIN_COUNT or
            manifest.get('source_commit') != branch['commit'] or
            manifest.get('payload_manifest_sha256') != stage.sha(HERE / 'manifest.json') or
            manifest.get('state_path') != str(stage.STATE) or
            manifest.get('frozen_project') != str(stage.FROZEN)):
        raise ValueError('stage targets another campaign boundary')


def validate_test_receipt(manifest_bytes: bytes) -> dict:
    receipt = json.loads((HERE / 'stage-test.json').read_bytes())
    command = receipt.get('command', [])
    if (receipt.get('kind') != 'copied-frozen-frontend-tests' or
            receipt.get('manifest_sha256') != stage.digest_bytes(manifest_bytes) or
            receipt.get('project') != '/home/grant/decomp/experiments/combined-frontier-frontend-20260926/project' or
            command[-(len(stage.TESTS) + 1):] != [*stage.TESTS, stage.FROZEN_TEST] or
            receipt.get('returncode') != 0 or receipt.get('passed') is not True):
        raise RuntimeError('copied frozen package tests did not pass for this stage')
    return receipt


def _current_hash(path: Path) -> str | None:
    return stage.sha(path) if path.is_file() else None


def _unchanged_state(state: dict) -> dict:
    return {key: value for key, value in state.items()
            if key not in ('pins', 'runtime_amendments')}


def apply(manifest: dict, manifest_bytes: bytes) -> dict:
    if not (stage.CONTROL / 'service.pause').is_file() or not (stage.NATIVE / 'service.pause').is_file():
        raise RuntimeError('both durable pause markers required')
    handles = [stage.lock(stage.CONTROL / 'resume-supervisor.lock'),
               stage.lock(stage.STATE.with_suffix('.lock'))]
    try:
        sys.path.insert(0, str(stage.FROZEN))
        from eval import campaign_state, completion_campaign
        pointer_before, launch_before, state, launch = stage.boundary(campaign_state, completion_campaign)
        if (stage.digest_bytes(pointer_before) != manifest['source_pointer_sha256'] or
                stage.digest_bytes(launch_before) != manifest['source_launch_sha256']):
            raise RuntimeError('checkpoint or launch moved since staging')
        if (state['inventory_sha256'] != manifest['inventory_sha256'] or
                state.get('model_digest') != manifest['model_digest'] or
                completion_campaign.digest(state['nodes']) != manifest['baseline_nodes_sha256'] or
                state['summary'] != manifest['baseline_summary'] or
                state.get('fast_metrics') != manifest['baseline_fast_metrics']):
            raise RuntimeError('inventory, model, nodes or metrics moved since staging')
        if len(state['pins']) != manifest['old_pin_count'] or stage.pin_digest(state['pins']) != manifest['old_pin_digest']:
            raise RuntimeError('old pin set moved since staging')
        old_files = {}
        new_pins = {}
        for rel, row in manifest['changed'].items():
            target = stage.FROZEN / rel
            old = target.read_bytes() if target.is_file() else None
            if (stage.digest_bytes(old) if old is not None else None) != row['old_sha256']:
                raise RuntimeError(f'old code bytes moved: {rel}')
            if (rel in stage.NEW and str(target) in state['pins']) or (
                    rel not in stage.NEW and state['pins'].get(str(target)) != row['old_sha256']):
                raise RuntimeError(f'old code pin moved: {rel}')
            if launch.get('code_hashes', {}).get(str(target)) != row['old_launch_sha256']:
                raise RuntimeError(f'old launch entry moved: {rel}')
            old_files[rel] = old
            new_pins[str(target)] = row['new_sha256']
        protected_before = copy.deepcopy(_unchanged_state(state))
        old_pins = dict(state['pins'])
        archive(HERE / 'campaign.before.json', pointer_before)
        archive(HERE / 'launch.before.json', launch_before)
        for rel, payload in old_files.items():
            if payload is not None:
                archive(HERE / 'previous-code' / rel, payload)

        written = set()
        pointer_after = None
        launch_after = None
        try:
            for rel, row in manifest['changed'].items():
                target = stage.FROZEN / rel
                payload = (stage.STAGED / rel).read_bytes()
                if stage.digest_bytes(payload) != row['new_sha256']:
                    raise RuntimeError(f'staged bytes moved during apply: {rel}')
                if rel in stage.NEW:
                    with target.open('xb') as stream:
                        written.add(rel)
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                else:
                    atomic_bytes(target, payload)
                    written.add(rel)
                if stage.sha(target) != row['new_sha256']:
                    raise RuntimeError(f'installed source differs: {rel}')
            state['pins'].update(new_pins)
            amendment = {
                'kind': '20260926-frontend-header-capabilities',
                'applied_at': time.time(), 'source_commit': manifest['source_commit'],
                'source_pointer_sha256': manifest['source_pointer_sha256'],
                'changed': manifest['changed'],
                'unchanged_pins_verified': manifest['unchanged_pins_verified'],
                'inventory_sha256': manifest['inventory_sha256'],
                'model_digest': manifest['model_digest'],
                'stage_test_sha256': stage.sha(HERE / 'stage-test.json'),
                'limits': 'Twelve frozen solver files only; no node, candidate, receipt, input, model, or ledger mutation.',
            }
            state.setdefault('runtime_amendments', []).append(amendment)
            campaign_state.Store(stage.STATE).save(state, changed=())
            pointer_after = stage.STATE.read_bytes()
            launch.setdefault('code_hashes', {}).update(new_pins)
            # Set the expected bytes before replacement: even if atomic()
            # raises after os.replace, this apply still owns the new launch.
            launch_after = campaign_state.encode(launch)
            campaign_state.atomic(stage.LAUNCH, launch)
            if stage.LAUNCH.read_bytes() != launch_after:
                raise RuntimeError('saved launch differs from reviewed hash update')
            importlib.reload(completion_campaign)
            repo = Path(state['config']['repo'])
            actual_pins = completion_campaign._pins(stage.FROZEN, repo)
            actual_pins.update(completion_campaign.frozen_wavefront.file_hashes([
                Path(path) for path in state['pins']
                if Path(path).is_relative_to(repo / 'nonmatchings')]))
            if actual_pins != state['pins']:
                raise RuntimeError('installed code/input pins differ from checkpoint')
            restored = campaign_state.read(stage.STATE)
            if (restored['pins'] != {**old_pins, **new_pins} or
                    _unchanged_state(restored) != protected_before or
                    restored.get('runtime_amendments', [])[-1] != amendment):
                raise RuntimeError('checkpoint round-trip changed protected state')
            if (json.loads(pointer_after)['summary'] != manifest['baseline_summary'] or
                    json.loads(pointer_after).get('fast_metrics') != manifest['baseline_fast_metrics']):
                raise RuntimeError('match count or metrics changed during amendment')
            amendment['result'] = {
                'commit': json.loads(pointer_after)['commit'],
                'pointer_sha256': stage.digest_bytes(pointer_after),
                'launch_sha256': stage.digest_bytes(launch_after),
                'new_pin_digest': stage.pin_digest(restored['pins']),
                'object_exact_or_integrated': restored['summary']['object_exact_or_integrated'],
                'function_exact_pending_integration': restored['summary']['function_exact_pending_integration'],
            }
            archive(HERE / 'amendment.json', (json.dumps(amendment, indent=2) + '\n').encode())
            return amendment['result']
        except BaseException as exc:
            # Never rewind a checkpoint or erase a file that another actor moved.
            if (stage.STATE.read_bytes() not in (pointer_before, pointer_after) or
                    stage.LAUNCH.read_bytes() not in (launch_before, launch_after) or
                    any(_current_hash(stage.FROZEN / rel) != manifest['changed'][rel]['new_sha256']
                        for rel in written)):
                raise RuntimeError('deployment bytes moved; manual locked recovery required') from exc
            for rel in reversed(stage.CHANGED):
                if rel not in written:
                    continue
                target = stage.FROZEN / rel
                if rel in stage.NEW:
                    target.unlink()
                else:
                    atomic_bytes(target, old_files[rel])
            if stage.STATE.read_bytes() != pointer_before:
                atomic_bytes(stage.STATE, pointer_before)
            if stage.LAUNCH.read_bytes() != launch_before:
                atomic_bytes(stage.LAUNCH, launch_before)
            archive(HERE / 'rollback.json', (json.dumps({
                'restored_pointer_sha256': stage.sha(stage.STATE),
                'restored_launch_sha256': stage.sha(stage.LAUNCH),
                'active_commit': json.loads(stage.STATE.read_bytes())['commit'],
                'error': f'{type(exc).__name__}: {exc}',
                'orphan_commit_possible': pointer_after is not None,
            }, indent=2) + '\n').encode())
            raise
    finally:
        for handle in reversed(handles):
            handle.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='mutate paused campaign explicitly')
    args = parser.parse_args()
    manifest_bytes = (HERE / 'stage.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    validate_payload(manifest, stage.STAGED)
    test = validate_test_receipt(manifest_bytes)
    if not args.apply:
        print(json.dumps({'mode': 'read-only', 'source_commit': manifest['source_commit'],
                          'changed': list(manifest['changed']), 'tests_passed': test['passed']}, indent=2))
        return
    print(json.dumps(apply(manifest, manifest_bytes), indent=2))


if __name__ == '__main__':
    main()
