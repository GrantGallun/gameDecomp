"""Equal compile-budget DEV comparison on preselected stalled campaign sources.

Run inside WSL with python -m eval.plateau_pilot --output NEW_DIR.
No campaign mutation, reference C reading, model calls or source integration.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time

from eval.agentrepair import _refuse_frozen_heldout
from solver import plateau, repair, rewrites, workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--campaign', type=Path,
        default=ROOT / 'eval/results/resume-pipeline-20260908/campaign.json')
    parser.add_argument('--baseline-db', type=Path,
        default=ROOT / 'eval/results/kb-sbk1-rom-ranges-v1.sqlite')
    parser.add_argument('--count', type=int, default=6)
    parser.add_argument('--budget', type=int, default=48)
    parser.add_argument('--min-proposals', type=int, default=0,
                        help='Preselect by unique existing rewrites, before experiment outcomes')
    args = parser.parse_args()
    if args.count < 1 or args.budget < 1:
        parser.error('count and budget must be positive')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    campaign = json.loads(args.campaign.read_text())
    history = sqlite3.connect(args.campaign.with_suffix('.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    candidates = []
    for function, node in campaign['nodes'].items():
        if node.get('status') != 'pending' or not 95 <= node.get('score', 0) < 100:
            continue
        jobs = node.get('jobs', [])
        if len(jobs) < 3:
            continue
        receipts = [json.loads(Path(j['receipt']).read_text()) for j in jobs[-2:]]
        scores = [r.get('best_residual', {}).get('weighted_progress_score') for r in receipts]
        if None in scores or abs(scores[0] - scores[1]) > .001:
            continue
        _refuse_frozen_heldout(ROOT / 'eval/sets', function)
        row = history.execute('SELECT source_code,diff_summary FROM attempts WHERE id=?',
                              (node['attempt_id'],)).fetchone()
        if not row or repair._digest(row[0]) != node['source_sha256']:
            continue
        initial_proposals = {repair._digest(rw(row[0])) for rw in rewrites.propose(row[0], row[1] or '')}
        initial_proposals.discard(node['source_sha256'])
        node = dict(node, initial_proposals=len(initial_proposals))
        if len(initial_proposals) < args.min_proposals:
            continue
        candidates.append((function, node, scores))
    history.close()
    candidates.sort(key=lambda row: (-row[1]['score'], row[0]))
    selected = candidates[:args.count]
    report = {'kind': 'plateau-paired-dev-v1', 'status': 'running',
        'selection': 'highest scores >=95 and <100, pending, >=3 jobs, last two job scores unchanged',
        'minimum_initial_unique_proposals': args.min_proposals,
        'regime': 'exposed DEV, existing headers/bootstrap drafts; not held-out or whole-game results',
        'budget_per_arm': args.budget, 'max_depth': 6, 'beam_width': 6,
        'plateau_config': asdict(plateau.Config()), 'model_calls': 0,
        'integration_requested': False, 'cases': [],
        'code_hashes': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                        for p in ('solver/plateau.py', 'solver/repair.py', 'solver/rewrites.py',
                                  'solver/workspace.py', 'eval/plateau_pilot.py')}}
    def save():
        pending = output / 'report.pending.json'
        pending.write_text(json.dumps(report, indent=2))
        pending.replace(output / 'report.json')
    # Freeze selection and source bytes before either arm sees any outcomes.
    for function, node, scores in selected:
        folder = output / function
        folder.mkdir()
        source = Path(node['source']).read_text()
        if repair._digest(source) != node['source_sha256']:
            raise ValueError('campaign source hash mismatch')
        (folder / 'original.c').write_text(source)
        report['cases'].append({'function': function, 'source_sha256': node['source_sha256'],
            'historical_score': node['score'], 'last_job_scores': scores, 'arms': {}})
        report['cases'][-1]['initial_unique_proposals'] = node['initial_proposals']
    del campaign, candidates, selected
    save()
    for case_index, case in enumerate(report['cases']):
        function = case['function']
        folder = output / function
        source = (folder / 'original.c').read_text()
        # Counterbalance ordering; same sources, toolchain, depth and compile cap.
        for arm in (('baseline', 'plateau') if case_index % 2 == 0 else ('plateau', 'baseline')):
            run = folder / arm
            repo = run / 'repo'
            repo.mkdir(parents=True)
            for name in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile',
                         'symbol_addrs.txt', 'snowboardkids.yaml', 'snowboardkids.z64',
                         'build', 'undefined_syms_auto.txt', 'undefined_syms.txt'):
                path = args.repo / name
                if path.exists():
                    (repo / name).symlink_to(path, target_is_directory=path.is_dir())
            ws = repo / 'nonmatchings' / function
            ws.mkdir(parents=True)
            for path in (args.repo / 'nonmatchings' / function).iterdir():
                if path.is_file() and (path.suffix == '.py' or path.name.startswith('target')
                        or path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm'}):
                    shutil.copy2(path, ws / path.name)
            db = sqlite3.connect(run / 'attempts.sqlite')
            original_db = sqlite3.connect(args.baseline_db.resolve().as_uri() + '?mode=ro', uri=True)
            original_db.backup(db)
            original_db.close()
            start_id = db.execute('SELECT coalesce(max(id),0) FROM attempts').fetchone()[0]
            start = time.monotonic()
            att, best, log = repair.search(repo, function, source, ws, conn=db,
                max_pairs=args.budget, max_depth=6, beam_width=6, verbose=False,
                run_id=f'plateau-pilot-{function}-{arm}',
                plateau_config=plateau.Config() if arm == 'plateau' else None)
            count = db.execute('SELECT count(*) FROM attempts WHERE id>?', (start_id,)).fetchone()[0]
            # Recompile the saved champion: workspace artifacts may belong to the
            # last rejected child. Persist fresh certificate/object for each arm.
            final = workspace.score(ws, repo, 'champion', best, conn=db, func=function,
                                    strategy='plateau-pilot-final', parent_attempt_id=att.receipt_id)
            db.close()
            (run / 'best.c').write_text(best)
            result = {'score': final.score, 'exact': plateau.verified(repair._State(best, final)),
                'compiled': final.compiled, 'frontend': final.frontend,
                'verification': final.verification, 'source_sha256': repair._digest(best),
                'rewritten_compiles': max(0, count - 1), 'elapsed_seconds': time.monotonic() - start,
                'log': log, 'final_diff': final.diff}
            case['arms'][arm] = result
            save()
            print(json.dumps({'function': function, 'arm': arm, 'score': final.score,
                              'exact': result['exact'], 'compiles': result['rewritten_compiles']}), flush=True)
    report['status'] = 'complete'
    save()


if __name__ == '__main__':
    main()
