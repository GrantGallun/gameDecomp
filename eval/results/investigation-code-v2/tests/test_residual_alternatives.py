from solver import residual_alternatives as alternatives, residual_sites
from solver.candidate_frontier import Candidate, Frontier
from eval import differential_repair_pilot as pilot
import re
import shutil
import subprocess
import pytest


def test_width_repair_requires_fresh_direct_instruction_evidence():
    source = 'typedef unsigned char u8;\nstruct T { u8 active; };\nvoid f(struct T *p) {\n p->active=1;\n}\n'
    diff = '@@ -1 +1 @@\n-sh v0,0x16(a0)\n+sb v0,0x16(a0)\n'
    direct = dict(status='verified', source_sha256=residual_sites.digest(source),
                  diff_sha256=residual_sites.digest(diff), instructions=[dict(
                      normalized_line=1, candidate_line=4, section='.text', address=0, bytes='00000000')])
    assert alternatives.representation(source, 'f', diff) == ()
    rows = alternatives.representation(source, 'f', diff, direct)
    assert len(rows) == 1
    assert 'u16 active;' in rows[0].source
    direct['source_sha256'] = 'stale'
    assert alternatives.representation(source, 'f', diff, direct) == ()


def test_cast_materialization_preserves_arithmetic_grouping():
    source = 'int f(int x) { return (int)((float)x * (((float)x / 65536.0f) / 65536.0f)); }'
    rows = alternatives.expression_lifetimes(source, 'f')
    assert len(rows) == 3
    assert all('/ 65536.0f) / 65536.0f)' in v.source for v in rows)
    assert not alternatives.expression_lifetimes('int f(int x) { return (int)g(x); }', 'f')
    assert not alternatives.expression_lifetimes('int f(volatile int x) { return (int)x; }', 'f')


def test_disjoint_local_coalescing_and_loop_guard():
    source = 'int f(int x) {\n int a;\n int b;\n if(x) { a=2; x+=a; } else { b=3; x+=b; } return x; }'
    rows = alternatives.expression_lifetimes(source, 'f')
    assert len(rows) == 1
    assert 'int b;' not in rows[0].source
    assert 'else { a=3; x+=a; }' in rows[0].source
    assert not alternatives.expression_lifetimes(source.replace('if(x)', 'while(x)'), 'f')


def test_structural_diff_gets_comparison_repairs_without_prototype_edits():
    source = 'int other(int a, int b);\nint f(int a, int b) { if(a<=b) {return 1;} else {return 0;} }'
    rows = pilot.deterministic_exactness_candidates(source, 'f', '-slt v0,a1,a0\n+slt v0,a0,a1')
    assert any('swap comparison' in r.label for r in rows)
    assert all(r.source.startswith('int other(int a, int b);') for r in rows)


def test_frontier_limits_same_output_but_keeps_one_neutral_continuation():
    f = Frontier(4, representatives_per_object=2)
    def c(name, asm):
        return Candidate(name, None, None, asm, name, (99,), name)
    assert f.offer(c('root', 'same'))
    assert f.pop().source == 'root'
    assert f.offer(c('neutral', 'same'))
    assert f.pop().source == 'neutral'
    assert not f.offer(c('inert', 'same'))
    assert f.offer(c('changed', 'new'))
    assert f.pop().source == 'changed'


@pytest.mark.parametrize('source', [
    'int f(int x) { return (int)((float)x * (((float)x / 65536.0f) / 65536.0f)); }',
    'int f(int x) {\n int a;\n int b;\n if(x) { a=2; x+=a; } else { b=3; x+=b; } return x; }',
    'int f(int x) {\n int q;\n int r;\n int value;\n value=x; q=value/60; r=value%60; return r; }',
])
def test_new_lifetimes_compile_and_execute_like_baseline(tmp_path, source):
    cc = shutil.which('clang')
    if not cc:
        pytest.skip('clang unavailable')
    variants = alternatives.expression_lifetimes(source, 'f') + alternatives.arithmetic_staging(source, 'f')
    assert variants
    units = [source] + [re.sub(r'\bf\b', 'f' + str(i), v.source) for i, v in enumerate(variants)]
    checks = ''.join(f'if (f(x)!=f{i}(x)) return {i+1};' for i in range(len(variants)))
    units.append('int main(void) { int x; for(x=-10000;x<10000;x++) {' + checks + '} return 0; }')
    src, exe = tmp_path / 'test.c', tmp_path / 'test.exe'
    src.write_text('\n'.join(units))
    subprocess.run([cc, '-std=c89', '-O2', str(src), '-o', str(exe)], check=True, capture_output=True)
    subprocess.run([str(exe)], check=True, capture_output=True)


def test_pairs_compose_disjoint_edits_and_reject_overlap():
    from solver.principle_variants import Variant
    source = 'int f(int x) { int y; y=x+1; return y*2; }'
    a = Variant('a', source.replace('x+1', '1+x'))
    b = Variant('b', source.replace('y*2', '2*y'))
    c = Variant('c', source.replace('x+1', '(x+1)'))
    pairs = alternatives.independent_pairs(source, [a, b, c])
    assert len(pairs) == 2
    assert any('1+x' in p.source and '2*y' in p.source for p in pairs)
    assert not alternatives.independent_pairs(source, [a, c])
    assert not alternatives.independent_pairs(source, [a, b], maximum=0)


def test_callback_panel_reaches_valid_memory_locations():
    import importlib.util
    from pathlib import Path
    from solver import mips_differential as differential
    path = Path(__file__).parents[1] / 'eval/experiments/code-shape-search/callback_panel.py'
    spec = importlib.util.spec_from_file_location('callback_panel', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = module.cases()
    assert len(rows) == 76
    for row in rows:
        differential._case_symbols(row)
    assert any(dict(r.entry_registers)['a1'] == 256 for r in rows)
    assert any(len(r.player_writes) == 10 for r in rows)


def test_fused_mask_attribution_follows_adjacent_selector_only():
    source = 'int f(int type) {\n u16 selector;\n u16 idx;\n type &= 0xFFFF;\n selector = type;\n idx = type;\n return selector + idx;\n}\n'
    diff = '@@ -1 +1 @@\n-andi t8,t7,0xff\n+andi t8,t7,0xffff\n'
    direct = dict(status='verified', source_sha256=residual_sites.digest(source),
        diff_sha256=residual_sites.digest(diff), instructions=[dict(normalized_line=1,
        candidate_line=4, section='.text', address=0, bytes='31f8ffff')])
    rows = alternatives.representation(source, 'f', diff, direct)
    assert len(rows) == 1
    assert 'u8 selector;' in rows[0].source and 'u16 idx;' in rows[0].source
    assert not alternatives.representation(source, 'f', diff.replace('-andi t8,t7', '-andi t9,t6'), direct)


def test_arithmetic_staging_declines_volatile_unsigned_and_aliasing():
    assert not alternatives.arithmetic_staging('int f(volatile int x) { return (int)(((float)x/3.0f)/7.0f); }', 'f')
    assert not alternatives.arithmetic_staging('int f(int x) { unsigned int q; unsigned int r; q=x/60; r=x%60; return r; }', 'f')
    assert not alternatives.arithmetic_staging('int f(int x) { int q; q=q/60; x=q%60; return x; }', 'f')


def test_masked_parameter_storage_preserves_explicit_truncation_and_prototype():
    source = 'int f(u16 type);\nint f(u16 type) { type &= 0xFFFF; return type; }'
    diff = '-sw a1,4(sp)\n-andi t7,a1,0xffff\n+move t7,a1'
    rows = alternatives.masked_parameter_storage(source, 'f', diff)
    assert len(rows) == 1
    assert rows[0].source.count('f(u32 type)') == 2
    assert 'type &= 0xFFFF;' in rows[0].source
    assert not alternatives.masked_parameter_storage(source.replace('type &=', 'g(type); type &='), 'f', diff)
    assert not alternatives.masked_parameter_storage(source, 'f', '')


def test_atomic_receipt_retries_transient_reader_lock(tmp_path, monkeypatch):
    from pathlib import Path
    import json
    original = Path.replace
    calls = []
    def replace(path, target):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError('transient reader')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(pilot.time, 'sleep', lambda _: None)
    path = tmp_path / 'receipt.json'
    pilot._atomic_json(path, {'complete': True})
    assert json.loads(path.read_text()) == {'complete': True}
    assert len(calls) == 2


def test_semantic_clean_champion_prioritizes_bytes_over_runtime_shape(monkeypatch):
    from solver.workspace import Attempt
    monkeypatch.setattr(pilot, '_observed_semantics_clean', lambda rows: True)
    monkeypatch.setattr(pilot, 'behavior_key', lambda rows, attempt: (76, 76, 0, 0, 0, 0, 0, rows[0], attempt.score))
    higher = Attempt(True, 93.702, False, '', '', '')
    lower = Attempt(True, 87.816, False, '', '', '')
    assert pilot.acceptance_key([-100], higher) > pilot.acceptance_key([0], lower)
