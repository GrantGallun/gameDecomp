"""Read-only target-assembly audit for repeated regions and embedded helper bodies.

No ground-truth C, model requests, builds, or campaign mutations. Findings are
assembly-similarity hypotheses, not recovered source or equivalence verdicts.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

from eval import campaign_state


def digest(raw):
    return hashlib.sha256(raw.encode('utf-8') if isinstance(raw, str) else raw).hexdigest()


def body(raw, name):
    """Extract from already hashed bytes, never reread the mutable source path."""
    text = raw.decode('utf-8')
    start = list(re.finditer(r'^\s*glabel\s+' + re.escape(name) + r'\s*$', text, re.M))
    if len(start) != 1:
        raise ValueError('target must contain exactly one named glabel')
    tail = text[start[0].start():]
    end = re.search(r'^\s*endlabel\s+' + re.escape(name) + r'\s*$', tail, re.M)
    result = tail[:end.start() if end else None].strip()
    if len(re.findall(r'^\s*glabel\s+', result, re.M)) != 1:
        raise ValueError('target contains another function before its endlabel')
    return result


def collect(run, out):
    """Snapshot immutable checkpoint references and retain verified target bytes."""
    run, out = Path(run).resolve(), Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    original = (run / 'campaign.json').read_bytes()
    pointer = json.loads(original)
    if pointer.get('kind') != campaign_state.KIND:
        raise ValueError('audit requires an immutable compact campaign checkpoint')
    store = (run / pointer['store']).resolve()
    if store.parent != run:
        raise ValueError('checkpoint store must remain inside the campaign run')
    pointer['store'] = str(store)
    (out / 'checkpoint-original.json').write_bytes(original)
    campaign_state.atomic(out / 'checkpoint.json', pointer)
    state = campaign_state.read(out / 'checkpoint.json')
    repo_name = state['config']['repo']
    native_repo = PurePosixPath(repo_name) if repo_name.startswith('/') else Path(repo_name)
    repo = (Path('//wsl.localhost/Ubuntu' + repo_name) if os.name == 'nt' and repo_name.startswith('/')
            else Path(repo_name)).resolve()
    base = repo / 'nonmatchings'
    targets = out / 'targets'
    targets.mkdir()
    assemblies, bindings, unavailable = {}, {}, []
    for name, node in sorted(state['nodes'].items()):
        path = base / name / 'target.s'
        pin_path = str(native_repo / 'nonmatchings' / name / 'target.s')
        try:
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_.$]*', name):
                raise ValueError('unsafe function name')
            if not path.resolve().is_relative_to(base.resolve()):
                raise ValueError('target path escapes nonmatchings')
            expected = state.get('pins', {}).get(pin_path)
            if not expected:
                raise ValueError('target assembly has no checkpoint pin')
            raw = path.read_bytes()
            if digest(raw) != expected:
                raise ValueError('target assembly differs from checkpoint pin')
            text = body(raw, name)
            # Annotated target files bind entry addresses as well as content.
            entry = re.search(r'/\*\s*[0-9a-fA-F]+\s+([0-9a-fA-F]{8})\s+[0-9a-fA-F]{8}\s*\*/', text)
            if entry and int(entry[1], 16) != node.get('address'):
                raise ValueError('target entry differs from checkpoint function address')
            saved = 'targets/' + digest(name) + '.s'
            (out / saved).write_bytes(raw)
            assemblies[name] = text
            bindings[name] = {'path': pin_path, 'saved_path': saved,
                              'target_sha256': expected, 'assembly_sha256': digest(text),
                              'address': node.get('address'), 'size': node.get('size'),
                              'status': node.get('status')}
        except (OSError, UnicodeError, ValueError) as exc:
            unavailable.append({'function': name, 'path': str(path), 'reason': str(exc)})
    inputs = {'schema_version': 1, 'checkpoint': pointer['commit'],
              'checkpoint_manifest_sha256': pointer['sha256'],
              'checkpoint_pointer_sha256': digest(original),
              'campaign_functions': len(state['nodes']), 'assemblies': assemblies,
              'bindings': bindings, 'unavailable': unavailable}
    campaign_state.atomic(out / 'assemblies.json', inputs)
    return inputs


def load_saved(path):
    """Validate every retained input without consulting live state or sources."""
    path = Path(path).resolve()
    inputs = json.loads(path.read_bytes())
    if inputs.get('schema_version') != 1:
        raise ValueError('unsupported assembly input schema')
    if set(inputs['assemblies']) != set(inputs['bindings']):
        raise ValueError('assembly and binding inventories differ')
    for name, text in inputs['assemblies'].items():
        binding = inputs['bindings'][name]
        if binding['saved_path'] != 'targets/' + digest(name) + '.s':
            raise ValueError('saved target must use its confined content inventory path')
        saved = (path.parent / binding['saved_path']).resolve()
        if not saved.is_relative_to(path.parent):
            raise ValueError('saved target escapes audit directory')
        raw = saved.read_bytes()
        if digest(raw) != binding['target_sha256'] or digest(text) != binding['assembly_sha256']:
            raise ValueError('saved target assembly changed: ' + name)
        if body(raw, name) != text:
            raise ValueError('saved target and extracted assembly differ: ' + name)
    return inputs


def analyse(inputs, out, *, min_instructions=8, large_instructions=128):
    from solver import inline_regions
    if not 4 <= min_instructions <= 32 or large_instructions < min_instructions:
        raise ValueError('instruction thresholds must satisfy 4 <= minimum <= 32 and minimum <= large')
    if load_saved(Path(out) / 'assemblies.json') != inputs:
        raise ValueError('analysis inputs differ from retained assembly inventory')
    report = inline_regions.analyse(inputs['assemblies'], min_instructions=min_instructions,
                                    large_instructions=large_instructions)
    envelope = {'schema_version': 1,
                'scope': 'Pinned target assembly only. Repeated regions and embedded bodies are '
                         'hypotheses, not source recovery or semantic equivalence.',
                'checkpoint': inputs['checkpoint'],
                'checkpoint_manifest_sha256': inputs['checkpoint_manifest_sha256'],
                'campaign_functions': inputs['campaign_functions'],
                'available_functions': len(inputs['assemblies']),
                'unavailable': inputs['unavailable'],
                'min_instructions': min_instructions, 'large_instructions': large_instructions,
                'assemblies_sha256': digest((Path(out) / 'assemblies.json').read_bytes()),
                'implementation_sha256': {
                    'eval/inline_regions.py': digest(Path(__file__).read_bytes()),
                    'solver/inline_regions.py': digest(Path(inline_regions.__file__).read_bytes()),
                    'eval/campaign_state.py': digest(Path(campaign_state.__file__).read_bytes())},
                'analysis': report}
    campaign_state.atomic(Path(out) / 'report.json', envelope)
    return envelope


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--run', type=Path)
    source.add_argument('--assemblies', type=Path, help='Replay retained assemblies.json without live state')
    parser.add_argument('--out', required=True, type=Path, help='New audit directory')
    parser.add_argument('--min-instructions', type=int, default=8)
    parser.add_argument('--large-instructions', type=int, default=128)
    args = parser.parse_args(argv)
    if not 4 <= args.min_instructions <= 32 or args.large_instructions < args.min_instructions:
        parser.error('instruction thresholds must satisfy 4 <= minimum <= 32 and minimum <= large')
    if args.run:
        inputs = collect(args.run, args.out)
    else:
        inputs = load_saved(args.assemblies)
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / 'targets').mkdir()
        for binding in inputs['bindings'].values():
            (args.out / binding['saved_path']).write_bytes((args.assemblies.parent / binding['saved_path']).read_bytes())
        campaign_state.atomic(args.out / 'assemblies.json', inputs)
    report = analyse(inputs, args.out, min_instructions=args.min_instructions,
                     large_instructions=args.large_instructions)
    print(json.dumps({key: report[key] for key in ('checkpoint', 'campaign_functions', 'available_functions')} |
                     {'unavailable': len(report['unavailable']), 'report': str(args.out / 'report.json')}))


if __name__ == '__main__':
    main()
