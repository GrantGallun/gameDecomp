"""Separate, model-guided hypotheses from retained candidates and target assembly.

These are NOT automatic search-arm wins. No reference C is read. Every hypothesis
uses the same frozen headers, target, strict frontend and byte certificate gate.
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest, runner
from eval.research_suite.compiler import NativeCompiler
from solver import regalloc_signature, repair_context


def edit_body(source, function, transform):
    start, end = repair_context.definition(source, function)
    begin = start.end()
    return source[:begin] + transform(source[begin:end - 1]) + source[end - 1:]


def pause(source, function):
    # Source is the retained coalescing child, before the search moved the next
    # palette initialization ahead of the previous call. The target's final
    # comparison uses literal 2 (li at,2), while the candidate uses 1.
    def merge(body):
        assert 's32 var_s0_2;' in body
        body = body.replace('s32 var_s0_2;', '')
        return re.sub(r'\bvar_s0_2\b', 'var_s0', body)

    def literal(body):
        prefix, found, suffix = body.rpartition('if (D_80121B57 == 1)')
        assert found
        return prefix + 'if (D_80121B57 == 2)' + suffix

    yield 'guided:pause-final-comparison-2', edit_body(source, function, literal)
    yield 'guided:pause-reuse-palette-local', edit_body(source, function, merge)
    yield 'guided:pause-reuse-and-comparison-2', edit_body(source, function, lambda b: literal(merge(b)))


def aerial(source, function):
    # Target lh v0 at 0x304 and no extra move; the retained child reused a
    # full-width velocity temporary for the later narrow timer value.
    old = '''temp_v0_2 = player->updateTimer;
    if (temp_v0_2 < 0x2D) {
        player->updateTimer = temp_v0_2 + 1;
    }'''
    assert old in source
    yield 'guided:aerial-reuse-narrow-timer-local', source.replace(old, old.replace('temp_v0_2', 'temp_v0'))
    yield 'guided:aerial-direct-timer-increment', source.replace(old, '''if (player->updateTimer < 0x2D) {
        player->updateTimer += 1;
    }''')
    for typ in ('s16', 'u16', 's32', 'u32'):
        def replace(body):
            return '\n    ' + typ + ' timer_value;' + body.replace(old, old.replace('temp_v0_2', 'timer_value'))
        yield 'guided:aerial-separate-timer-' + typ, edit_body(source, function, replace)


def hold(source, function):
    # Preserve rereading after the callback. Test storing the raw timer and
    # leaving the bit test as an expression instead of a named boolean local.
    for mask in (1, 2, 3):
        def rewrite(body):
            for index, (name, call) in enumerate((('temp_v0', '0x18'), ('temp_v0_2', '0x16'))):
                if not mask & (1 << index):
                    continue
                body = body.replace(f'var_t6 = {name} & 2;', '')
                needle = f'setRaceMotionAnimation((RaceMotionState *) player, {call});\n                    var_t6 = player->subStateTimer & 2;'
                assert needle in body
                body = body.replace(needle, f'setRaceMotionAnimation((RaceMotionState *) player, {call});\n                    {name} = player->subStateTimer;')
                # Locate the test following this arm's load, not the other arm.
                offset = body.index(f'{name} = player->subStateTimer;')
                at = body.index('if (var_t6 != 0)', offset)
                body = body[:at] + body[at:].replace('if (var_t6 != 0)', f'if ({name} & 2)', 1)
            return body
        yield f'guided:hold-raw-timer-mask-{mask}', edit_body(source, function, rewrite)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    specs = [(3, 'gradient', None, pause), (2, 'gradient', 'gradient', aerial),
             (0, 'noop', 'noop', hold)]
    reports = []
    for index, root_role, best_role, proposals in specs:
        bundle = args.experiment / f'bundle-{index:02d}'
        payload, identity = runner.checked_bundle(bundle, args.repo)
        task = next(t for t in payload['tasks'] if t['provenance']['root_role'] == root_role)
        source = manifest.task_source(bundle, task)
        if best_role:
            path = args.experiment / f'run-{index:02d}/t{index:02d}_{best_role}/0/evolvability_coalesce/result.json'
            source = json.loads(path.read_text())['best_source']
        candidates = list(proposals(source, task['function']))
        compiler = NativeCompiler(args.repo, task, bundle, args.output / task['function'],
                                  budget=1 + len(candidates), identity=identity)
        baseline = compiler(source, 'guided-parent')
        observations = []
        for label, candidate in candidates:
            compiled = compiler(candidate, label, source)
            row = {'label': label, 'compiled': compiled.compiled, 'exact': compiled.exact,
                   'gradient': list(regalloc_signature.compare(compiler.target_dump, compiled.dump).gradient)
                               if compiled.compiled and compiled.dump else None}
            observations.append(row)
            print(task['function'], row, flush=True)
        runner.checked_bundle(bundle, args.repo)
        report = {'function': task['function'], 'source_sha256': manifest.digest(source.encode()),
                  'kind': 'model-guided-binary-residual-hypotheses', 'assistance': 'unknown',
                  'model_assisted': True, 'reference_source_used': False,
                  'baseline_compiled': baseline.compiled, 'observations': observations,
                  'costs': compiler.costs(), 'attempts': compiler.rows}
        reports.append(report)
        manifest.write_json(args.output / 'results.json', reports)


if __name__ == '__main__':
    main()
