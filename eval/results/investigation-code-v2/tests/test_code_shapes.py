import ctypes
import json
import os
import re
import shutil
import subprocess

import pytest

from solver import code_shapes, transition_policy
from eval import differential_repair_pilot as pilot
from solver.workspace import Attempt


def test_structural_residual_enters_code_shape_search_with_hard_budget():
    source = 'int f(int x) { while (x > 2) { x--; } return x; }'
    diff = '-lw v0,0(a0)\n+lw v0,4(a0)'
    assert not pilot.register_or_local_order_only(diff)
    variants = pilot.deterministic_exactness_candidates(source, 'f', diff)
    assert {v.label.split('@')[0] for v in variants} == {
        'code-shape:while-for', 'code-shape:while-top-check'}
    assert pilot.deterministic_exactness_candidates(source, 'f', diff, 1) == variants[:1]
    assert pilot.deterministic_exactness_candidates(source, 'f', diff, 0) == ()
    assert len({v.source for v in variants}) == len(variants)
    assert len({transition_policy.action_family('', v.label) for v in variants}) == 2


@pytest.mark.parametrize('body', [
    'if (i == 2) continue;', 'goto done;', 'done: i++;',
])
def test_for_conversion_declines_control_transfers(body):
    source = 'int f(int n) { int i; for (i=0;i<n;i++) {' + body + '} return i; }'
    assert not any('for-while' in v.label for v in code_shapes.candidates(source, 'f'))


def test_scope_and_lexical_boundaries():
    prefix = '#define f(x) while(x) {}\nint other(int x) { while(x) {x--;} return x; }\n'
    source = prefix + 'int f(int x) { const char *s="/* while(x) {} */"; while(x) {x--;} return x; }'
    variants = code_shapes.candidates(source, 'f')
    assert len(variants) == 2
    assert all(v.source.startswith(prefix) and '"/* while(x) {} */"' in v.source for v in variants)
    assert not code_shapes.candidates(source, 'missing')
    assert not code_shapes.candidates('int f(int x) {\n#if X\nwhile(x) {x--;}\n#endif\n}', 'f')
    assert not code_shapes.candidates('int f(int n) { for(int i=0;i<n;i++) {} return n; }', 'f')
    # No common-type-changing expansion of general ternary expressions.
    assert not code_shapes.candidates('long f(int x) { return x ? -1 : 1U; }', 'f')


@pytest.mark.parametrize('fallback', [False, True, 'patch', 'bad-patch'])
def test_real_generators_reach_exact_after_rejecting_semantic_regression(tmp_path, monkeypatch, fallback):
    source = 'int f(int n) { while (n > 0) { n--; } return n; }'
    variants = code_shapes.candidates(source, 'f', allow_do_while=False)
    model_source = source.replace('return n;', 'return (int)n;')
    clean = 'li v0,1\njr ra\nnop\n'
    wrong = 'li v0,2\njr ra\nnop\n'
    source_path = tmp_path / 'root.c'
    source_path.write_text(source)
    (tmp_path / 'target_object_dump_normalized.s').write_text(clean)
    monkeypatch.setattr(pilot.refine, 'ensure_schema', lambda conn: None)
    monkeypatch.setattr(pilot.workspace, 'bootstrap', lambda *a: tmp_path)
    monkeypatch.setattr(pilot.workspace, 'target_asm', lambda *a: clean)
    monkeypatch.setattr(pilot.workspace, 'semantic_assembly', lambda text, path: text)
    monkeypatch.setattr(pilot.project_headers, 'prompt_context', lambda *a: '')
    monkeypatch.setattr(pilot, '_binary_observed_offsets', lambda *a: {})
    monkeypatch.setattr(pilot.exactness_gradient, 'receipt_history', lambda *a, **kw: ())
    scored = []

    def score(ws, repo, tag, code, **kwargs):
        scored.append(code)
        bad = code == variants[0].source or (code == model_source and fallback == 'bad-patch')
        exact = (code == variants[1].source and not fallback) or (code == model_source and fallback == 'patch')
        (ws / f'{tag}_object_dump_normalized.s').write_text(wrong if bad else clean)
        return Attempt(True, 100 if bad or exact else 50, exact,
                       '-beq a0,zero,20\n+j 24', '', '', receipt_id=100 + len(scored))

    monkeypatch.setattr(pilot.workspace, 'score', score)
    model_prompts = []

    def generate(endpoint, model, prompt, **kwargs):
        assert scored[:3] == [source] + [v.source for v in variants]
        model_prompts.append(prompt)
        if fallback is True:
            raise RuntimeError('controlled unavailable fallback')
        if len(model_prompts) == 1:
            return '\n'.join([
                '1. NEXT BAD OBSERVABLE: none; branch residual only',
                '2. TARGET VALUE/ADDRESS PROVENANCE: target branch',
                '3. CANDIDATE VALUE/ADDRESS PROVENANCE: candidate branch',
                '4. SOURCE-LEVEL CAUSE: test alternate return spelling',
                '5. MINIMAL PATCH PLAN: explicit int return cast',
            ]), {'done_reason': 'stop', 'eval_count': 1}
        return json.dumps({'kind': 'expression', 'hypothesis': 'test return spelling',
                           'edits': [{'old': 'return n;', 'new': 'return (int)n;'}]}), {
                               'done_reason': 'stop', 'eval_count': 1}

    monkeypatch.setattr(pilot.llm, 'generate', generate)
    monkeypatch.setattr(pilot.workspace, 'assert_uncontaminated', lambda *a: None)
    monkeypatch.setattr(pilot.attempt_receipts, 'record_model_proposal', lambda *a, **kw: 999)
    monkeypatch.setattr(pilot.attempt_receipts, 'link_model_proposal', lambda *a, **kw: None)
    receipt = pilot.run(
        repo=tmp_path, db=tmp_path / 'attempts.sqlite', source_path=source_path,
        source_parent_attempt_id=100, output=tmp_path / 'receipt.json',
        best_source_out=tmp_path / 'best.c', model='unused', endpoint='unused',
        rounds=1, timeout=1, think='high', num_thread=1, temperature=0,
        diagnosis_num_predict=1, patch_num_predict=1, patch_retries=0,
        compiler_retries=0, max_stalls=1, seed=1, cache_dir=None, function='f',
        cases=(pilot.differential.TestCase('case', 1),), call_arities={},
        return_registers=('v0',), deterministic_only=not fallback,
        compiler_response_policy=pilot.transition_policy.TransitionPolicy(()))
    assert scored[:3] == [source] + [v.source for v in variants]
    if fallback:
        assert receipt['result']['exact'] == (fallback == 'patch')
        assert len(model_prompts) == (1 if fallback is True else 2)
        assert 'MISMATCH-GUIDED SOURCE ALTERNATIVES' in model_prompts[0]
        assert 'semantic-regression' in model_prompts[0]
        assert 'residual-unchanged' in model_prompts[0]
        assert receipt['source_targeted_fallbacks'][0]['source_sha256'] == pilot._sha(source)
        if fallback is not True:
            assert scored[-1] == model_source
            assert 'MISMATCH-GUIDED SOURCE ALTERNATIVES' in model_prompts[1]
            outcome = receipt['iterations'][-1]['source_intervention']['outcome']
            assert outcome == ('byte-exact' if fallback == 'patch' else 'semantic-regression')
        return
    assert receipt['result']['exact']
    assert receipt['recorded_tokens'] == 0
    assert (tmp_path / 'best.c').read_text() == variants[1].source
    rows = receipt['deterministic_exactness']
    assert not rows[0]['accepted_for_next_round']
    assert not rows[0].get('offered_to_frontier', False)
    assert rows[1]['accepted_for_next_round']
    assert rows[0]['source_intervention']['outcome'] == 'semantic-regression'
    assert rows[1]['source_intervention']['outcome'] == 'byte-exact'
    assert receipt['mismatch_source_maps'][0]['sites']


SOURCES = [
    'int f(int n) { int i; int s=0; for(i=0;i<n;i++){if(i==7) break; s+=i;} return s; }',
    'int f(int n) { int i; int s=0; for(i=0;i<n;i++){int i=2; s+=i;} return s; }',
    'int f(int n) { int s=0; while(n-- > 0) { if(n==3) continue; s+=n; } return s+n; }',
    'int f(int n) { volatile int checks=0; while(++checks < n) { if(checks==7) break; } return checks; }',
    'int f(int n) { if(n++ > 0) { return n; } else { return -n; } }',
    'int f(int n) { return n++ > 0 ? 1 : 0; }',
    'int f(int n) { int a[4]; int i; a[0]=7;a[1]=8;a[2]=9;a[3]=10;i=n&3;return a[i]; }',
    'int f(double n) { if(n < 0.0) { return 1; } else { return 2; } }',
]


@pytest.mark.parametrize('source', SOURCES)
@pytest.mark.parametrize('optimization', ['-O0', '-O2'])
@pytest.mark.parametrize('allow_do_while', [False, True])
def test_generated_implementations_execute_like_baseline(tmp_path, source, optimization, allow_do_while):
    """Real compiler and execution, including NaN, continue and shadowed locals."""
    clang = shutil.which('clang')
    if not clang or (os.name == 'nt' and not shutil.which('lld-link')):
        pytest.skip('native clang/linker unavailable')
    variants = code_shapes.candidates(source, 'f', allow_do_while=allow_do_while)
    assert variants
    sources = [source] + [v.source for v in variants]
    unit = '\n'.join(re.sub(r'\bf\b', f'f{i}', s) for i, s in enumerate(sources))
    src = tmp_path / 'variants.c'
    src.write_text('int _fltused=0;\n' + unit)
    library = tmp_path / ('variants.dll' if os.name == 'nt' else 'variants.so')
    if os.name == 'nt':
        obj = tmp_path / 'variants.obj'
        subprocess.run([clang, '-std=c89', optimization, '-c', str(src), '-o', str(obj)],
                       check=True, capture_output=True)
        subprocess.run([shutil.which('lld-link'), '/dll', '/noentry', '/nodefaultlib',
                        '/out:' + str(library), str(obj)] +
                       [f'/export:f{i}' for i in range(len(sources))],
                       check=True, capture_output=True)
    else:
        subprocess.run([clang, '-std=c89', optimization, '-shared', '-fPIC', str(src),
                        '-o', str(library)], check=True, capture_output=True)
    lib = ctypes.CDLL(str(library))
    floating = 'double n' in source
    functions = [getattr(lib, f'f{i}') for i in range(len(sources))]
    for fn in functions:
        fn.argtypes = [ctypes.c_double if floating else ctypes.c_int]
        fn.restype = ctypes.c_int
    inputs = list(range(-8, 25)) + ([float('nan'), float('inf'), -float('inf')] if floating else [])
    for value in inputs:
        expected = functions[0](value)
        for variant, fn in zip(variants, functions[1:]):
            assert fn(value) == expected, (variant.label, value, variant.source)
