"""Explicit paused/drained adoption of freshly verified external pilot sources.

Invoke this script directly in WSL, after reviewed code deployment. Imports come
only from RUN/code. Existing private DBs are never imported or copied. Compiler
attempts append to the current ledger; canonical game sources remain untouched.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def load_candidates(path):
    rows = json.loads(Path(path).read_bytes())
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        raise ValueError('expected one to three explicitly reviewed candidates')
    seen = set()
    for row in rows:
        name = row['function']
        if not re.fullmatch(r'[A-Za-z_]\w*', name) or name in seen:
            raise ValueError('invalid or repeated function')
        seen.add(name)
        for key in ('source_sha256', 'expected_parent_source_sha256'):
            if not re.fullmatch('[0-9a-f]{64}', row[key]):
                raise ValueError('missing source identity')
        source = Path(row['source']).read_text()
        if sha(source) != row['source_sha256']:
            raise ValueError('reviewed candidate bytes changed: ' + name)
        row['_source'] = source
    return rows


def paused_drained(run, state):
    service = json.loads((run / 'service.json').read_bytes())
    if (service.get('status') != 'paused' or service.get('worker_pid')
            or not (run / 'service.pause').is_file()
            or state.get('inflight') or state.get('fast_inflight')):
        raise ValueError('campaign must already be paused and fully drained')


def candidate_gate(attempt):
    certificate = attempt.verification or {}
    return (attempt.compiled and (attempt.frontend or {}).get('passed') is True
            and (bool(attempt.exact and certificate.get('exact'))
                 or certificate.get('function_boundary', {}).get('function_exact') is True))


def ratchet(before, after):
    """Keep every previously exact/integrated binding, not only the total count."""
    for name, binding in before.items():
        node = after[name]
        if (node['status'], node.get('source_sha256'), node.get('attempt_id')) != binding:
            raise ValueError('existing verified binding changed: ' + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--integrate', action='store_true',
                        help='run the existing bounded prior-union integration sweep after adoption')
    args = parser.parse_args()
    if os.name != 'posix':
        raise ValueError('run directly in WSL with native compiler workspaces')
    run = args.run.resolve(strict=True)
    frozen = (run / 'code').resolve(strict=True)
    sys.path.insert(0, str(frozen))
    from eval import campaign_state, campaign_workers, completion_campaign as campaign
    from eval import fast_campaign, campaign_integration, semantic_lane
    from solver import workspace, residual, modelrepair
    for module in (campaign_state, campaign_workers, campaign, fast_campaign,
                   campaign_integration, semantic_lane, workspace, residual, modelrepair):
        if not Path(module.__file__).resolve().is_relative_to(frozen):
            raise ValueError('module did not load from the deployed frozen code: ' + module.__name__)
    output = args.out.absolute()
    if str(output).startswith('/mnt/') or output.exists():
        raise ValueError('output must be a new native WSL directory')
    candidates = load_candidates(args.manifest)
    state_path = run / 'campaign.json'
    with campaign.campaign_lock(state_path.with_suffix('.lock')):
        state = campaign_state.read_for_resume(state_path)
        paused_drained(run, state)
        repo, db = Path(state['config']['repo']), Path(state['config']['db'])
        if Path(state['config']['project']).resolve() != frozen:
            raise ValueError('configured project differs from deployed frozen code')
        if db.resolve() != (run / 'campaign.sqlite').resolve():
            raise ValueError('unexpected live attempt ledger')
        campaign.frozen_wavefront.verify_files(state['pins'])
        pins = campaign._pins(frozen, repo)
        pins.update(campaign.frozen_wavefront.file_hashes([
            Path(p) for p in state['pins'] if Path(p).is_relative_to(repo / 'nonmatchings')]))
        if pins != state['pins']:
            raise ValueError('current frozen inputs differ from checkpoint pins')
        protected = {n: (v['status'], v.get('source_sha256'), v.get('attempt_id'))
                     for n, v in state['nodes'].items()
                     if v['status'] in {'object_exact', 'integrated'}}
        output.mkdir(parents=True, exist_ok=False)
        output = output.resolve()
        if str(output).startswith('/mnt/'):
            raise ValueError('resolved output is not native WSL storage')
        initial_pointer = state_path.read_bytes()
        (output / 'before-pointer.json').write_bytes(initial_pointer)
        (output / 'reviewed-manifest.json').write_bytes(args.manifest.read_bytes())
        run_id = 'external-pilot-adoption-' + str(time.time_ns())
        results = []
        with sqlite3.connect(db, timeout=120) as conn:
            inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
            if campaign.digest(inventory) != state['inventory_sha256']:
                raise ValueError('function inventory changed')
            old_matches = conn.execute("SELECT COUNT(*) FROM functions WHERE state='matched'").fetchone()[0]
            # Validate every current parent before appending any compiler work.
            for row in candidates:
                node = state['nodes'][row['function']]
                if row['function'] in protected:
                    row['_skip'] = 'already verified; existing binding retained'
                    continue
                parent = conn.execute('SELECT a.source_code,a.source_sha256,f.name FROM attempts a '
                    'JOIN functions f ON f.addr=a.func_addr WHERE a.id=?', (node['attempt_id'],)).fetchone()
                if (parent is None or parent[2] != row['function']
                        or parent[1] != node['source_sha256'] or sha(parent[0]) != parent[1]
                        or parent[1] != row['expected_parent_source_sha256']
                        or sha(Path(node['source']).read_text()) != parent[1]):
                    raise ValueError('current parent differs from reviewed source: ' + row['function'])
                row['_parent_attempt_id'] = node['attempt_id']
            for row in candidates:
                name = row['function']
                if row.get('_skip'):
                    results.append({'function': name, 'status': 'skipped', 'reason': row['_skip']})
                    continue
                folder = output / name
                folder.mkdir()
                candidate_path = folder / 'source.c'
                candidate_path.write_text(row['_source'])
                private_repo = campaign_workers.isolate(repo, folder / 'repo', name)
                ws = private_repo / 'nonmatchings' / name
                tag = 'adoption_' + str(time.time_ns())
                context = {k: v for k, v in row.items() if not k.startswith('_')}
                context.update(current_parent_attempt_id=row['_parent_attempt_id'],
                               frozen_pin_sha256=campaign.digest(pins), model_calls=0,
                               live_database_copied=False, canonical_sources_changed=False)
                started = time.monotonic()
                try:
                    attempt = workspace.score(ws, private_repo, tag, row['_source'], conn=conn,
                        func=name, parent_attempt_id=row['_parent_attempt_id'],
                        strategy='external-pilot-frozen-recheck', run_id=run_id,
                        relation='reviewed-source-recheck', action=row['mechanism'],
                        run_kind='external-pilot-adoption', extra=context)
                except Exception as exc:
                    attempt = workspace.Attempt(False, 0.0, False, '',
                                                f'{type(exc).__name__}: {exc}', '')
                    workspace.record_attempt(conn, name, row['_source'], attempt,
                        parent_attempt_id=row['_parent_attempt_id'], run_id=run_id,
                        strategy='external-pilot-recheck-exception', relation='reviewed-source-recheck',
                        extra=context)
                if attempt.receipt_id is None:
                    raise ValueError('compiler attempt was not recorded')
                campaign_state.atomic(folder / 'attempt.json', asdict(attempt))
                (folder / 'diff.txt').write_text(attempt.diff or '')
                (folder / 'compiler.txt').write_text(attempt.compiler_stderr or '')
                packet = residual.build(attempt, target_asm=workspace.target_asm(ws, name),
                    target_object=ws / 'target.o', candidate_object=ws / (tag + '.o')
                    if attempt.compiled else None).to_dict()
                semantic = None
                if attempt.compiled and (attempt.frontend or {}).get('passed') is True:
                    deferred = semantic_lane.DeferredPanel(private_repo, ws, name,
                        max_cases=64, max_steps=10000)
                    try:
                        semantic = deferred(modelrepair.CandidateState(row['_source'], attempt, ws / (tag + '.o')))
                    except Exception as exc:
                        semantic = {'status': 'unavailable', 'authoritative': False,
                            'source_sha256': row['source_sha256'],
                            'reason': f'fresh semantic evaluation failed: {type(exc).__name__}: {exc}'}
                result = {'function': name, 'status': 'evaluated', 'attempt_id': attempt.receipt_id,
                    'source': str(candidate_path), 'source_sha256': row['source_sha256'],
                    'score': attempt.score, 'exact': attempt.exact, 'verification': attempt.verification,
                    'residual': packet, 'semantic_validation': semantic,
                    'calls_attempted': 0, 'best_score_improved': attempt.score > state['nodes'][name].get('score', 0),
                    'wall_seconds': time.monotonic() - started, 'provenance': context,
                    'candidate_gate': candidate_gate(attempt)
                        and (attempt.verification or {}).get('candidate_source_sha256') == row['source_sha256'],
                    'whole_rom_verified': False}
                campaign_state.atomic(folder / 'result.json', result)
                results.append(result)
                print(json.dumps({k: result[k] for k in ('function', 'attempt_id', 'score', 'exact', 'candidate_gate')}), flush=True)
            matches = conn.execute("SELECT COUNT(*) FROM functions WHERE state='matched'").fetchone()[0]
            if matches != old_matches:
                raise ValueError('compile-only adoption unexpectedly changed KB match count')
        # All receipts survive a failed gate; no checkpoint acceptance is partial.
        if any(r.get('status') != 'skipped' and not r['candidate_gate'] for r in results):
            campaign_state.atomic(output / 'report.json', {'status': 'recheck_failed', 'results': results})
            raise ValueError('one or more fresh candidates failed the exact/frontend gate; no state adopted')
        paused_drained(run, state)
        campaign.frozen_wavefront.verify_files(state['pins'])
        if state_path.read_bytes() != initial_pointer:
            raise ValueError('checkpoint pointer changed despite campaign lock')
        changed = []
        for result in results:
            if result['status'] == 'skipped':
                continue
            source = Path(result['source']).read_text()
            if sha(source) != result['source_sha256']:
                raise ValueError('fresh rechecked source changed')
            receipt = run / 'campaign-artifacts' / (run_id + '-' + result['function'] + '.json')
            campaign_state.atomic(receipt, result)
            campaign.accept(state['nodes'][result['function']],
                {'name': 'external_pilot_frozen_recheck'}, result, receipt)
            changed.append(result['function'])
        ratchet(protected, state['nodes'])
        amendment = {'kind': 'fresh-external-pilot-adoption', 'run_id': run_id,
            'out': str(output), 'functions': changed, 'time': time.time(),
            'prior_pointer_sha256': sha(initial_pointer), 'pins_sha256': campaign.digest(pins),
            'model_calls': 0, 'whole_rom_verified': False, 'private_database_import': False,
            'canonical_sources_changed': False, 'budgets_reset': False}
        state.setdefault('runtime_amendments', []).append(amendment)
        selected = fast_campaign.project(state)
        fast_campaign.summary(state, selected)
        store = campaign_state.Store(state_path)
        store.save(state, changed=changed)
        if args.integrate:
            integration_changed = campaign_integration.sweep(state, repo=repo, db=db,
                artifacts=run / 'campaign-artifacts', checkpoint=store.commit)
            ratchet(protected, state['nodes'])
            selected = fast_campaign.project(state)
            fast_campaign.summary(state, selected)
            store.save(state, changed=integration_changed)
        campaign_state.atomic(output / 'report.json', {'status': 'adopted', 'amendment': amendment,
            'results': results, 'statuses': {name: state['nodes'][name]['status'] for name in changed},
            'integration_sweep': state.get('integration_sweep') if args.integrate else None})
        print(json.dumps({'status': 'adopted', 'functions': changed, 'out': str(output)}), flush=True)


if __name__ == '__main__':
    main()
