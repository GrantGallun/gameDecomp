"""Run with python -m eval.research_suite --help."""
import argparse
import json
from pathlib import Path

from . import manifest


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as out:
        json.dump(value, out, indent=2, allow_nan=False)
        out.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Opt-in compiler-backed research experiments; no campaign writes')
    sub = parser.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze', help='capture candidates, objects, headers and compiler identity')
    freeze.add_argument('--config', required=True, type=Path)
    freeze.add_argument('--repo', required=True, type=Path)
    freeze.add_argument('--output', required=True, type=Path)
    for name in ('run', 'key-audit'):
        p = sub.add_parser(name)
        p.add_argument('--bundle', required=True, type=Path)
        p.add_argument('--repo', required=True, type=Path)
        p.add_argument('--output', required=True, type=Path)
        p.add_argument('--budget', type=int)
        if name == 'run':
            p.add_argument('--arms', nargs='+')
            p.add_argument('--seeds', nargs='+', type=int)
    for name in ('panels', 'proposals', 'summarize'):
        p = sub.add_parser(name)
        p.add_argument('--input', required=True, type=Path)
        p.add_argument('--output', required=True, type=Path)
        if name == 'summarize':
            p.add_argument('--baseline', default='production')
    reconstruct = sub.add_parser('reconstruct', help='recover a complete normalized candidate listing from a complete diff')
    reconstruct.add_argument('--target', required=True, type=Path)
    reconstruct.add_argument('--diff', required=True, type=Path)
    reconstruct.add_argument('--target-sha256')
    reconstruct.add_argument('--output', required=True, type=Path)
    smoke = sub.add_parser('smoke', help='synthetic native IDO integration controls, not yield evidence')
    smoke.add_argument('--repo', required=True, type=Path)
    smoke.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == 'freeze':
        from .compiler import environment
        config = read(args.config)
        # Paths in task configuration are relative to that configuration file.
        for task in config['tasks']:
            for field in ('source', 'target_object', 'context'):
                if task.get(field):
                    task[field] = str((args.config.parent / task[field]).resolve())
            for proposal in task.get('proposals', []):
                proposal['source'] = str((args.config.parent / proposal['source']).resolve())
        payload = manifest.freeze(config, args.output,
                                  identity=environment(args.repo, [t['compile_target'] for t in config['tasks']]))
        result = {'tasks': len(payload['tasks']), 'bundle': str(args.output),
                  'sha256': manifest.fingerprint(payload)}
    elif args.command == 'run':
        from .runner import run_bundle
        result = run_bundle(args.bundle, args.repo, args.output, arms=args.arms,
                            seeds=args.seeds, budget=args.budget)
    elif args.command == 'key-audit':
        from .compiler import NativeCompiler
        from .key_audit import audit, layout_variants
        from .runner import checked_bundle
        payload, identity = checked_bundle(args.bundle, args.repo)
        args.output.mkdir(parents=True, exist_ok=False)
        result = []
        for task in payload['tasks']:
            checked_bundle(args.bundle, args.repo)
            budget = args.budget if args.budget is not None else 8
            compiler = NativeCompiler(args.repo, task, args.bundle, args.output / task['id'],
                                      budget=budget, identity=identity)
            source = manifest.task_source(args.bundle, task)
            variants = list(layout_variants(source)) + [
                (p.get('label', 'recorded'), manifest.inside(args.bundle, p['source']).read_text(encoding='utf-8'))
                for p in task['proposals']]
            row = audit(source, variants, compiler, compiler.key, compiler.same_object, budget=budget)
            valid, reason = True, None
            try:
                checked_bundle(args.bundle, args.repo)
            except ValueError as exc:
                valid, reason = False, str(exc)
            row.update(task=task['id'], valid=valid, invalid_reason=reason, **compiler.costs(),
                       bundle_sha256=manifest.fingerprint(payload),
                       environment_sha256=manifest.fingerprint(identity))
            manifest.write_json(compiler.output / 'audit.json', row)
            result.append(row)
            manifest.write_json(args.output / 'audits.json', result)
            if not valid:
                raise ValueError(reason)
    elif args.command == 'panels':
        from .counterexamples import compare_panels
        result = compare_panels(**read(args.input))
        save_new(args.output, result)
    elif args.command == 'proposals':
        from .proposals import compose, pure_variants, infer_views, type_variants
        request = read(args.input)
        report = infer_views(request.get('accesses', []), request.get('flows', []))
        result = {'constraints': report,
                  'types': type_variants(request['source'], request['function'], report),
                  'composed': compose(request['source'], lambda s: pure_variants(s, request['function'])),
                  'scope': 'uncertified source hypotheses; use a native run to evaluate'}
        save_new(args.output, result)
    elif args.command == 'reconstruct':
        from .metrics import measure
        result = measure(args.target.read_text(encoding='utf-8'), args.diff.read_text(encoding='utf-8'),
                         expected_target_sha256=args.target_sha256)
        save_new(args.output, result)
    elif args.command == 'summarize':
        from .runner import summarize
        result = summarize(read(args.input), baseline=args.baseline)
        result['input_authority'] = 'unverified result JSON; this command does not recertify native artifacts'
        save_new(args.output, result)
    else:
        from .smoke import run
        result = run(args.repo, args.output)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
