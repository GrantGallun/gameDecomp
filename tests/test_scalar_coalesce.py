"""Source-only controls; native motivating receipts live in the experiment directory."""
import importlib

import pytest

from solver import regalloc_mutations, regalloc_search
from eval.research_suite import runner


# Reduced from retained m2c candidate t004 (attempt 255793), not reference C.
MOTIVE = """void f(State *state) {
    s16 temp_v0;
    s32 var_t1;
    s32 temp_h0;
    temp_v0 = *(s16 *)((u8 *)state + 0x466);
    var_t1 = temp_v0 - 1;
    if (temp_v0 == 0) {
        loadFrame(state);
        temp_h0 = *(s16 *)((u8 *)state + 0x466);
        var_t1 = temp_h0 - 1;
    }
    *(s16 *)((u8 *)state + 0x466) = var_t1;
}
"""
SIMPLE = """int f(int x) {
    int a;
    int b;
    a = x;
    use(a);
    b = x + 1;
    return b;
}
"""


def variants(source, limit=12):
    return list(importlib.import_module('solver.scalar_coalesce').variants(source, 'f', limit))


def test_fires_on_motivating_narrow_local_and_labels_type_change():
    rows = variants(MOTIVE)
    assert len(rows) == 1
    label, family, candidate = rows[0]
    assert family == 'scalar_coalesce'
    assert 'temp_h0->temp_v0' in label and 's32->s16' in label
    assert 'temp_h0' not in candidate
    assert 'temp_v0 = *(s16 *)' in candidate and 'var_t1 = temp_v0 - 1;' in candidate


def test_owned_tokens_only_preserve_members_comments_literals_and_other_function():
    source = SIMPLE.replace('    return b;', '''    obj.b = b;
    ptr->b = b;
    say("b // comment", "/* b */"); /* b */
    return b;''') + '\nint helper(void) { int b; b = 2; return b; }\n'
    [(_, _, candidate)] = variants(source)
    assert 'obj.b = a;' in candidate and 'ptr->b = a;' in candidate
    assert 'say("b // comment", "/* b */"); /* b */' in candidate
    assert candidate.endswith('int helper(void) { int b; b = 2; return b; }\n')
    assert '    int b;' not in candidate and 'return a;' in candidate


@pytest.mark.parametrize('source', [
    SIMPLE.replace('    return b;', '    return a + b;'),  # overlapping uses
    SIMPLE.replace('use(a);', 'use(&a);'),
    SIMPLE.replace('return b;', 'use(&(b)); return b;'),
    SIMPLE.replace('return b;', '{ int b; b = 2; use(b); } return b;'),
    SIMPLE.replace('return b;', '{ int z, b; b = 2; use(b); } return b;'),
    SIMPLE.replace('return b;', '{ int (*b)(void); use(b); } return b;'),
    SIMPLE.replace('return b;', '{ int (b); b = 9; use(b); } return b;'),
    ('typedef int scalar;\n' + SIMPLE).replace('return b;', '{ scalar (b); b = 9; use(b); } return b;'),
    ('typedef int scalar, other;\n' + SIMPLE).replace('return b;', '{ scalar (b); b = 9; use(b); } return b;'),
    ('typedef struct { int x; } scalar, other;\n' + SIMPLE).replace('return b;', '{ scalar (b); use(b); } return b;'),
    ('#define USE(v) use(&(v))\n' + SIMPLE).replace('use(a);', 'USE(a);'),
    SIMPLE.replace('int b;', 'static int b;'),
    SIMPLE.replace('int b;', 'volatile int b;'),
    SIMPLE.replace('int b;', 'int b[2];'),
    SIMPLE.replace('int b;', 'float b;'),
    SIMPLE.replace('b = x + 1;', 'b += x + 1;'),
    SIMPLE.replace('b = x + 1;', 'b = b + 1;'),
    SIMPLE.replace('a = x;', 'while (x--) { a = x;').replace('return b;', 'use(b); } return x;'),
    SIMPLE.replace('a = x;', 'again: a = x;').replace('return b;', 'if (x--) goto again; return b;'),
    SIMPLE.replace('a = x;', '#define USE b\n    a = x;'),
    SIMPLE.replace('int b;', 'int b = 2;'),
])
def test_declines_ambiguous_bindings_escapes_overlap_and_backedges(source):
    assert variants(source) == []


def test_limit_and_determinism():
    source = SIMPLE.replace('int b;', 'int b;\n    int c;').replace('return b;', 'use(b); c = x; return c;')
    assert len(variants(source)) == 3
    assert variants(source, 1) == variants(source)[:1]
    assert variants(source, 0) == []


def test_family_is_opt_in_in_existing_round_robin_stream():
    assert all(kind != 'scalar_coalesce' for _, kind, _ in regalloc_mutations.variants(MOTIVE, 'f'))
    rows = list(regalloc_mutations.variants(MOTIVE, 'f', coalesce=True))
    assert any(kind == 'scalar_coalesce' and 'temp_h0' not in candidate for _, kind, candidate in rows)


@pytest.mark.parametrize('arm', ['production', 'evolvability', 'production_coalesce', 'evolvability_coalesce'])
def test_factorial_arms_share_engine_oracle_budget_and_lineage(monkeypatch, arm):
    # Exercise the real family through the real search. Isolate competing families
    # in this control so the only exact candidate is offered by coalescing.
    generator = regalloc_mutations.variants
    monkeypatch.setattr(regalloc_mutations, 'variants', lambda *args, **kwargs:
                        (row for row in generator(*args, **kwargs) if row[1] == 'scalar_coalesce'))
    monkeypatch.setattr(regalloc_mutations, 'enabling_variants', lambda *args: [])
    calls = []

    class Compiler:
        target_dump = 'f:\n  addiu v0,a0,1\n  jr ra\n  nop\n'
        key = staticmethod(lambda s: s)
        same_object = staticmethod(lambda a, b: a.obj == b.obj)

        def __call__(self, source, label, parent_source=None):
            calls.append((source, label, parent_source))
            exact = 'temp_h0' not in source
            dump = self.target_dump if exact else self.target_dump.replace(',1', ',2')
            return regalloc_search.Compiled(True, exact, dump, obj=dump.encode())

    result = runner.run_arm('f', MOTIVE, Compiler(), arm=arm, budget=8, seed=0)
    enabled = arm.endswith('_coalesce')
    assert result['exact'] is enabled
    assert result['policy']['coalesce'] is enabled
    assert result['policy']['enable'] and result['policy']['keyed']
    assert result['policy']['selection'] == ('evolvability' if arm.startswith('evolvability') else 'gradient')
    assert result['compiles'] == len(calls) == (2 if enabled else 1)
    assert result['budget_spent'] == pytest.approx(len(calls) * 1.14)
    if enabled:
        assert calls[1][2] == MOTIVE and calls[1][1].startswith('scalar_coalesce:')


@pytest.mark.parametrize('selection', ['gradient', 'evolvability'])
def test_coalescing_switch_survives_conclusive_key_restart(monkeypatch, selection):
    seen_options, calls = [], []

    def generate(source, *args, coalesce=False, **kwargs):
        seen_options.append(coalesce)
        if not coalesce:
            return []
        return {'root': [('bridge', 'scalar_coalesce', 'bridge')],
                'bridge': [('winner', 'scalar_coalesce', 'winner')]}.get(source, [])

    target = 'f:\n  addiu v0,a0,1\n  jr ra\n  nop\n'

    def compile_candidate(source, label):
        calls.append(source)
        dump = target if source != 'root' else target.replace(',1', ',2')
        return regalloc_search.Compiled(True, source == 'winner', dump, obj=source.encode())

    monkeypatch.setattr(regalloc_mutations, 'variants', generate)
    result = regalloc_search.search('f', 'root', compile_candidate, target, budget=4,
                                   coalesce=True, selection=selection, key=lambda s: 'collision',
                                   key_cost=0, audit_rate=1, same_object=lambda a, b: a.obj == b.obj)
    assert result.exact and result.key_violations == 1
    assert result.compiles == len(calls) == 4
    assert seen_options and all(seen_options)

