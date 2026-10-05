"""Run the authorized expanded investigation through ordinary campaign.execute."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path('/mnt/c/Code/gameDecomp')
NATIVE = Path('/home/grant/decomp/experiments/investigation-loop-20260927')
REPO = Path('/home/grant/decomp/sbk1')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--functions', nargs='+', default=['__osPopThread', 'drawRaceTypeSelectPortrait',
                                                          'requestMusicSequenceBank', '__osSiDeviceBusy'])
    parser.add_argument('--turns', type=int, default=12)
    parser.add_argument('--revision', default='v1')
    args = parser.parse_args()
    if not args.revision.isalnum():
        raise ValueError('revision must be alphanumeric')
    run_root = NATIVE / args.revision
    run_root.mkdir(exist_ok=True)
    code = run_root / 'code'
    if not code.exists():
        for folder in ('solver', 'eval', 'oracle', 'kb', 'miner', 'patterns', 'tests', 'tools'):
            shutil.copytree(ROOT / folder, code / folder,
                ignore=shutil.ignore_patterns('__pycache__', 'results', '*.sqlite', '*.db', '.pytest_cache'))
        if (ROOT / 'pytest.ini').exists():
            shutil.copy2(ROOT / 'pytest.ini', code / 'pytest.ini')
        pins = {str(p.relative_to(code)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in code.rglob('*.py')}
        (run_root / 'code-pins.json').write_text(json.dumps(pins, indent=2) + '\n')
    sys.path.insert(0, str(code))
    from eval import campaign_workers, completion_campaign
    from solver import workspace, investigation, llm
    manifest = json.loads((NATIVE / 'manifest.json').read_text())
    rows = {r['function']: r for r in manifest['selected']}
    reports = []
    for name in args.functions:
        row = rows[name]
        node = row['node']
        workspace.bootstrap(REPO, name)
        repo = campaign_workers.isolate(REPO, NATIVE / 'model-repo', name)
        directory = run_root / 'model' / name
        directory.mkdir(parents=True, exist_ok=False)
        config = dict(manifest['source_config'], repo=str(repo), db=str(NATIVE / 'canary.sqlite'),
            project=str(code), endpoint=llm.host(), experiment_memory_root=str(NATIVE / 'notebooks'))
        profile = {'name': 'investigate', 'model': True, 'investigate': True, 'deterministic_budget': 0,
            'lane': row['lane'], 'investigation_policy': investigation.policy(args.turns, 16, 900),
            'capability_issues': {k: v for k, v in manifest['shared_issues'].items()
                                  if name in v['affected_functions'] and len(v['affected_functions']) >= 2},
            'brief': 'Use direct compiler observations to distinguish causes. Prefer a structural alternative '
                     'when prior local search failed. Construct a target-first input when behavior or environment '
                     'is uncertain. Request a shared tooling repair when source changes cannot address the obstruction.'}
        print(json.dumps({'event': 'started', 'function': name, 'lane': row['lane'],
                          'baseline_score': node.get('score'), 'turns': args.turns}), flush=True)
        started = time.monotonic()
        try:
            result = completion_campaign.execute(repo=repo, db=NATIVE / 'canary.sqlite', function=name,
                node=node, profile=profile, config=config, out=directory / 'result.json')
            (directory / 'campaign-result.json').write_text(json.dumps(result, indent=2) + '\n')
            summary = {'function': name, 'lane': row['lane'], 'baseline_score': node.get('score'),
                'score': result.get('score'), 'exact': result.get('exact'),
                'model_calls': result.get('calls_attempted'), 'invalid': result.get('invalid_proposals'),
                'semantic_status': (result.get('semantic_validation') or {}).get('status'),
                'observations': [o['kind'] for o in result.get('investigation', {}).get('observations', [])]}
        except Exception as exc:
            summary = {'function': name, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
            import traceback
            (directory / 'error.txt').write_text(traceback.format_exc())
        summary['seconds'] = time.monotonic() - started
        reports.append(summary)
        (run_root / 'canary-summary.json').write_text(json.dumps(reports, indent=2) + '\n')
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
