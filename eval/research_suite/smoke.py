"""Native synthetic positive/negative controls for integration, never held-out answers."""
from pathlib import Path
import sys

from solver import compiler_experiment
from .compiler import NativeCompiler, environment, _run
from . import manifest
from .key_audit import audit, layout_variants
from .proposals import compose, pure_variants, infer_views, type_variants
from .runner import ARMS, run_bundle, checked_bundle


TARGET = 'build/src/engine/viewport_manager.o'


def run(repo, output):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    if sys.platform != 'linux' or str(output).startswith('/mnt/'):
        raise ValueError('smoke requires native WSL/Linux artifacts outside /mnt')
    output.mkdir(parents=True, exist_ok=False)
    identity = environment(repo, [TARGET])
    recipe = identity['recipes'][TARGET]
    source = 'unsigned int research_add(unsigned int x) { return x + 2U; }\n'
    winner = source.replace('2U', '1U')

    def object_for(name, content):
        work = output / ('target-' + name)
        work.mkdir()
        context = work / 'context'
        context.mkdir()
        raw, converted, obj = work / 'source.c', work / 'candidate.c', work / 'target.o'
        raw.write_text(compiler_experiment._compile_source(repo, content), encoding='utf-8')
        _run([sys.executable, str(repo / 'tools/textconv.py'), str(repo / 'tools/charmap.txt'),
              str(raw), str(converted)], work)
        command = [*compiler_experiment._direct_command(recipe['command'], repo, context),
                   '-o', str(obj), str(converted)]
        _run(command, work)
        manifest.write_json(work / 'target-receipt.json', {'synthetic': True, 'command': command,
                            'source_sha256': manifest.digest(content.encode()),
                            'object_sha256': manifest.digest(obj.read_bytes())})
        return obj

    root = output / 'root.c'
    proposal = output / 'proposal.c'
    root.write_text(source, encoding='utf-8')
    proposal.write_text(winner, encoding='utf-8')
    obj = object_for('add', winner)
    config = {'tasks': [{'id': 'synthetic_add', 'function': 'research_add', 'source': str(root),
                         'target_object': str(obj), 'compile_target': TARGET, 'assistance': 'synthetic',
                         'provenance': {'kind': 'synthetic positive and negative controls'},
                         'proposals': [{'source': str(proposal), 'label': 'synthetic-positive'}]}],
              'settings': {'arms': list(ARMS), 'seeds': [0], 'budget': 4}}
    bundle = output / 'bundle'
    payload = manifest.freeze(config, bundle, identity=identity)
    summary = run_bundle(bundle, repo, output / 'paired')
    task = payload['tasks'][0]
    controls = NativeCompiler(repo, task, bundle, output / 'controls', budget=3, identity=identity)
    bad = controls(source, 'negative', None)
    good = controls(winner, 'positive', None)
    invalid = controls('this is not valid C;', 'compile-failure', None)
    checks = {'negative_rejected': bad.compiled and not bad.exact,
              'positive_certified': good.compiled and good.exact,
              'compiler_failure_logged': not invalid.compiled and len(controls.rows) == 3}

    key_compiler = NativeCompiler(repo, task, bundle, output / 'key-stress', budget=5, identity=identity)
    key_result = audit(source, layout_variants(source), key_compiler, key_compiler.key,
                       key_compiler.same_object, budget=5)
    manifest.write_json(output / 'key-stress/audit.json', {**key_result, **key_compiler.costs()})
    checks['same_key_pairs_certified'] = key_result['conclusive'] > 0 and key_result['violations'] == 0

    # __LINE__ is a required negative stress control: changing effective line
    # numbers changes the computed return value and must change this key.
    line_source = 'unsigned int research_add(unsigned int x) { return x + __LINE__; }\n'
    line_compiler = NativeCompiler(repo, task, bundle, output / 'line-stress', budget=2, identity=identity)
    line_result = audit(line_source, [('line1000', '#line 1000 "candidate.c"\n' + line_source)],
                        line_compiler, line_compiler.key, line_compiler.same_object, budget=2)
    manifest.write_json(output / 'line-stress/audit.json', {**line_result, **line_compiler.costs()})
    checks['line_macro_changes_key'] = line_result['different_key_pairs'] == 1

    domains = [
        ('composition', 'unsigned int f(unsigned int x) { return (x + 1U) + 2U; }\n', []),
        ('types', 'unsigned int f(unsigned char *p) { return *(unsigned int *)(p + 4); }\n',
         [{'base': 'f:p', 'offset': 4, 'width': 4, 'signed': False, 'kind': 'load', 'evidence_id': 'synthetic-lw@0x10'}]),
    ]
    for name, original, accesses in domains:
        original_path = output / (name + '.c')
        original_path.write_text(original, encoding='utf-8')
        target = object_for(name, original)
        frozen = output / (name + '-bundle')
        data = manifest.freeze({'tasks': [{'id': name, 'function': 'f', 'source': str(original_path),
                                          'target_object': str(target), 'compile_target': TARGET,
                                          'assistance': 'synthetic', 'accesses': accesses}]}, frozen, identity=identity)
        compiler = NativeCompiler(repo, data['tasks'][0], frozen, output / (name + '-proposals'),
                                  budget=4, identity=identity)
        variants = (compose(original, lambda s: pure_variants(s, 'f')) if name == 'composition' else
                    type_variants(original, 'f', infer_views(accesses)))
        observations = []
        for variant in variants:
            compiled = compiler(variant['source'], variant['label'], original)
            observations.append({'proposal': variant, 'compiled': compiled.compiled, 'exact': compiled.exact})
        manifest.write_json(compiler.output / 'proposals.json', observations)
        checks[name + '_emits_certified_source'] = bool(observations) and all(r['exact'] for r in observations)
    checked_bundle(bundle, repo)
    report = {'kind': 'native-synthetic-integration-controls', 'passed': all(checks.values()),
              'checks': checks, 'summary': summary, 'identity_sha256': manifest.fingerprint(identity),
              'scope': 'wiring and motivating controls only; no evidence of increased campaign yield',
              'output': str(output)}
    manifest.write_json(output / 'smoke.json', report)
    if not report['passed']:
        raise AssertionError('native smoke failed: ' + str(checks))
    return report
