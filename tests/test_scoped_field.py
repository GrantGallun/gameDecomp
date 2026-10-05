import importlib
import importlib.util
from pathlib import Path

import pytest


SOURCE = '''typedef int s32;
typedef short s16;
typedef struct Player { int velocity; short timer; int other; } Player;
void f(Player *p) {
    s32 temp;
    temp = p->velocity;
    consume(temp);
    temp = p->timer;
    if (temp < 45) {
        p->timer = temp + 1;
    }
}
'''


def rows(source=SOURCE, limit=8):
    assert importlib.util.find_spec('solver.scoped_field'), 'assignment-scoped generator is missing'
    return list(importlib.import_module('solver.scoped_field').variants(source, 'f', limit=limit))


def test_fires_on_reused_temporary_and_preserves_earlier_assignment_and_declaration():
    candidates = rows()
    assert candidates
    for label, family, candidate in candidates:
        assert family == 'scoped_field'
        assert 's32 temp;' in candidate
        assert 'temp = p->velocity;\n    consume(temp);' in candidate
        assert 'temp = p->timer;' not in candidate
        assert 'if (((s32)(p->timer)) < 45)' in candidate
        assert 'p->timer = ((s32)(p->timer)) + 1;' in candidate


def test_stops_at_unconditional_redefinition_without_touching_later_role():
    source = SOURCE.replace('\n}', '\n    temp = 9;\n    consume(temp);\n}')
    assert any('temp = 9;\n    consume(temp);' in c for _, _, c in rows(source))


def test_preserves_narrowing_conversion_and_ignores_member_names_and_comments():
    source = SOURCE.replace('s32 temp;', 's16 temp;').replace('if (temp < 45)', '/* temp stays in comment */\n    if (temp < 45)')
    assert any('((s16)(p->timer))' in c and '/* temp stays in comment */' in c for _, _, c in rows(source))


@pytest.mark.parametrize('source', [
    SOURCE.replace('if (temp < 45)', 'touch(p);\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', '(*callback)();\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', '(callback)();\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'callbacks[0]();\n    if (temp < 45)'),
    SOURCE.replace('int other;', 'int callback;').replace('if (temp < 45)', 'callback();\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'p->timer = 3;\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'p->other += 1;\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', '*alias = 1;\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'alias[0] = 1;\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'p = other;\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'if (temp < 45 && touch(p))'),
    SOURCE.replace('p->timer = temp + 1;', 'p->timer = temp + 1;\n        consume(temp);'),
    SOURCE.replace('s32 temp;', 'volatile s32 temp;'),
    SOURCE.replace('short timer;', 'volatile short timer;'),
    SOURCE.replace('consume(temp);', 'consume(&temp);'),
    SOURCE.replace('if (temp < 45)', 'if ((temp = 3) < 45)'),
    SOURCE.replace('if (temp < 45)', 'if (temp < 45) { int temp; temp = 3; }\n    if (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'while (temp < 45)'),
    SOURCE.replace('if (temp < 45)', 'again: if (temp < 45)'),
    '#define timer other\n' + SOURCE,
])
def test_declines_barriers_aliases_volatile_shadows_macros_and_backedges(source):
    assert not rows(source)


def test_does_not_delete_read_before_redefinition_or_conditional_definition():
    assert not rows(SOURCE.replace('p->timer = temp + 1;', 'temp = temp + 1;'))
    assert not rows(SOURCE.replace('temp = p->timer;', 'if (p->other) temp = p->timer;'))


def test_limit_zero_is_empty_and_results_are_deterministic():
    assert not rows(limit=0)
    assert rows() == rows()


def test_real_motivating_parent_is_reachable_in_normal_stream_and_can_be_ablated():
    from solver import regalloc_mutations
    source = (Path(__file__).parent / 'fixtures/scoped_field_aerial.c').read_text()
    candidates = list(regalloc_mutations.variants(source, 'updateRacePlayerMode48AerialTrick'))
    assert any(kind == 'scoped_field' and '((s32)(player->updateTimer))' in candidate
               for _, kind, candidate in candidates)
    assert not any(kind == 'scoped_field' for _, kind, _ in regalloc_mutations.variants(
        source, 'updateRacePlayerMode48AerialTrick', scoped_fields=False))


@pytest.mark.parametrize('arm', ['production', 'beam', 'pure', 'compose', 'types'])
def test_shared_search_and_research_policy_expose_the_same_ablation(monkeypatch, arm):
    from solver import regalloc_mutations, regalloc_search
    from eval.research_suite import runner
    monkeypatch.setattr(runner.construction, 'pure_variants', lambda *a: [])
    monkeypatch.setattr(runner.construction, 'type_variants', lambda *a: [])
    generate = regalloc_mutations.variants
    monkeypatch.setattr(regalloc_mutations, 'variants', lambda *a, **kw:
                        (row for row in generate(*a, **kw) if row[1] == 'scoped_field'))
    monkeypatch.setattr(regalloc_mutations, 'enabling_variants', lambda *a: [])

    class Compiler:
        target_dump = 'f:\n  jr ra\n  nop\n'
        key = staticmethod(lambda source: source)
        same_object = staticmethod(lambda a, b: a.obj == b.obj)

        def __call__(self, source, label, parent_source=None):
            exact = '((s32)(p->timer))' in source
            dump = self.target_dump if exact else 'f:\n  addiu v0,a0,1\n  jr ra\n  nop\n'
            return regalloc_search.Compiled(True, exact, dump, obj=dump.encode())

    for enabled in (False, True):
        result = runner.run_arm('f', SOURCE, Compiler(), arm=arm, budget=8, seed=0,
                                options={'scoped_fields': enabled})
        assert result['exact'] is enabled
        assert result['policy']['scoped_fields'] is enabled


def test_typedef_member_name_does_not_hide_a_real_call():
    source = '''typedef struct P { int callback; int x; } P;
int callback(void);
int f(P *p) { int temp; temp=p->x; if (callback()) {} return temp; }'''
    assert not rows(source)
