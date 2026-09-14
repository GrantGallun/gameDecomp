"""Replay final frozen-v3 compiler failures with bounded deterministic repairs.

Uses saved candidate bodies only, never reference bodies. Does not mutate the
campaign, frozen code, baseline DB, or integrate game source.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from eval import agentrepair
from solver import frontend_repair, repair_context, workspace


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', type=int, required=True)
    args = parser.parse_args()
    root = Path('/mnt/c/Code/gameDecomp')
    repo = Path('/home/grant/decomp/sbk1')
    output = root/f'eval/results/frontend-v3-replay-v{args.version}.json'
    if output.exists() or args.version < 1:
        raise ValueError('positive unused version required')
    rows = []
    checkpoints = []
    with sqlite3.connect(root/'eval/results/kb-sbk1-range-replay-v1.sqlite', timeout=120) as conn:
        for batch in (1, 2):
            checkpoint = root/f'eval/results/failure-coverage-fresh-paired-v3-batch-{batch}.json'
            checkpoints.append({'path': str(checkpoint), 'sha256': sha(checkpoint)})
            state = json.loads(checkpoint.read_text())
            if state.get('inflight'):
                raise ValueError('campaign still active')
            for function, node in state['nodes'].items():
                residual = node.get('residual') or {}
                frontend = residual.get('frontend') or {}
                row = {'function': function, 'batch': batch, 'original_status': node['status'],
                       'original_compiled': residual.get('compiled'),
                       'original_frontend_passed': frontend.get('passed'),
                       'compiler_error': residual.get('compiler_error_signature'),
                       'frontend_errors': [s for s in frontend.get('diagnostics', '').splitlines() if 'error:' in s],
                       'semantic_status': (node.get('semantic_validation') or {}).get('status'),
                       'blocker': node.get('blocker'), 'probes': []}
                rows.append(row)
                if node['status'] == 'parked' or (residual.get('compiled') and frontend.get('passed')):
                    continue
                agentrepair._refuse_frozen_heldout(root/'eval/sets', function)
                path = Path(node['source'])
                source = path.read_text(encoding='utf-8')
                if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
                    raise ValueError('saved source changed: '+function)
                row.update(original_source=str(path), original_source_sha256=node['source_sha256'])
                # Check activation before launching a compiler; full diagnostics
                # will be refreshed for selected candidates below.
                preview = frontend_repair.propose(repo, source, function, frontend.get('diagnostics', ''), big_endian_o32=True)
                normalized = repair_context.normalize(source, residual.get('compiler_error_signature') or '', function)
                if not preview['changes'] and not normalized:
                    row['replay_status'] = 'no_applicable_bounded_repair'
                    continue
                ws = workspace.bootstrap(repo, function)
                target_sha = sha(ws/'target.o')
                tag = 'frontend_probe_'+str(time.time_ns())
                base = workspace.score(ws, repo, tag, source, conn=conn, func=function,
                    strategy='frontend-v3-root', extra={'checkpoint': str(checkpoint), 'source_sha256': node['source_sha256']})
                row['root_attempt_id'] = base.receipt_id
                report = frontend_repair.propose(repo, source, function,
                    (base.frontend or {}).get('diagnostics', ''),
                    big_endian_o32=frontend_repair.big_endian_o32(ws/'target.o'))
                variants = repair_context.normalize(source, base.compiler_stderr, function)
                if report['changes']:
                    variants.append(('frontend-representation', report['source']))
                for index, (label, candidate) in enumerate(variants):
                    child = workspace.score(ws, repo, tag+'_'+str(index), candidate, conn=conn, func=function,
                        strategy='frontend-v3-child', parent_attempt_id=base.receipt_id,
                        relation='compiler-normalization', action=label,
                        extra={'hypothesis': report if label == 'frontend-representation' else label})
                    row['probes'].append({'label': label, 'attempt_id': child.receipt_id,
                        'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                        'source_path': str(ws/(tag+'_'+str(index)+'.c')),
                        'compiled': child.compiled, 'frontend': child.frontend,
                        'compiler_stderr': child.compiler_stderr, 'score': child.score,
                        'semantic_status': 'not_tested'})
                    # Clang truncates after its diagnostic error limit. Refresh
                    # diagnostics and continue the same bounded representation
                    # chain, retaining actual parentage, rather than guessing
                    # undisplayed source sites. Production uses four rounds too.
                    for depth in range(1, 4):
                        if (child.frontend or {}).get('passed'):
                            break
                        follow = frontend_repair.propose(repo, candidate, function,
                            (child.frontend or {}).get('diagnostics', ''),
                            big_endian_o32=frontend_repair.big_endian_o32(ws/'target.o'))
                        if not follow['changes']:
                            break
                        candidate = follow['source']
                        parent_id = child.receipt_id
                        child_tag = tag+'_'+str(index)+'_r'+str(depth)
                        child = workspace.score(ws, repo, child_tag, candidate, conn=conn, func=function,
                            strategy='frontend-v3-child', parent_attempt_id=parent_id,
                            relation='compiler-normalization', action='frontend-representation', extra={'hypothesis': follow})
                        row['probes'].append({'label': 'frontend-representation', 'attempt_id': child.receipt_id,
                            'parent_attempt_id': parent_id, 'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                            'source_path': str(ws/(child_tag+'.c')), 'compiled': child.compiled,
                            'frontend': child.frontend, 'compiler_stderr': child.compiler_stderr,
                            'score': child.score, 'semantic_status': 'not_tested'})
                row['target_sha256'] = target_sha
                row['target_unchanged'] = target_sha == sha(ws/'target.o')
                if not row['target_unchanged']:
                    raise ValueError('target changed')
                row['replay_status'] = 'probed'
                agentrepair._atomic_json(output, {'rows': rows, 'checkpoints': checkpoints, 'status': 'running'})
                print(json.dumps({'function': function, 'probes': [
                    {k: p[k] for k in ('label', 'compiled', 'score')} | {'frontend': (p['frontend'] or {}).get('passed')}
                    for p in row['probes']]}), flush=True)
    if any(sha(Path(c['path'])) != c['sha256'] for c in checkpoints):
        raise ValueError('campaign changed')
    agentrepair._atomic_json(output, {'rows': rows, 'checkpoints': checkpoints, 'status': 'complete',
        'model_calls': 0, 'integration_requested': False, 'reference_bodies_used': False,
        'scope': 'exposed-source compile-only replay; original outcomes retained; not semantic or autonomous acceptance'})


if __name__ == '__main__':
    main()
