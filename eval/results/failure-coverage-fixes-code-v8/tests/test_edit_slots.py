import json

import pytest

from solver import edit_slots, modelrepair


def proposal(slot, new):
    return modelrepair.parse_proposal(json.dumps({'kind':'declarations', 'hypothesis':'test',
        'edits':[{'slot':slot,'new':new}]}))


def test_duplicate_lines_have_distinct_bound_locations():
    source = 'void f(void) {\n    work();\n    work();\n}\n'
    slot = next(k for k in edit_slots.slots(source) if k.endswith(':L3'))
    changed = modelrepair.apply_proposal(source, proposal(slot, '    other();'))
    assert changed.count('work();') == 1 and 'other();' in changed
    with pytest.raises(ValueError, match='stale'):
        modelrepair.apply_proposal(source+'\n', proposal(slot, '    other();'))


def test_declaration_insertion_needs_no_header_typedef_anchor():
    source = '#include "actor.h"\nvoid f(Actor *a) { a->timer=0; }\n'
    slot = next(k for k in edit_slots.slots(source) if k.endswith(':DECLARATIONS'))
    changed = modelrepair.apply_proposal(source, proposal(slot, 'struct Actor {char pad[42]; short timer;};\n'))
    assert changed.startswith('#include "actor.h"\nstruct Actor')
    with pytest.raises(ValueError, match='forbidden'):
        proposal(slot, '#include "answer.c"\n')


def test_slots_cannot_rewrite_whole_file_or_exceed_budget():
    source = 'void f(void) { return; }'
    slot = next(k for k in edit_slots.slots(source) if k.endswith(':L1'))
    with pytest.raises(ValueError, match='whole-file'):
        modelrepair.apply_proposal(source, proposal(slot, 'void f(void) {}'))
    source = 'void f(void) {\n' + ' '*12000 + 'return;\n}\n'
    slot = next(k for k in edit_slots.slots(source) if k.endswith(':L2'))
    with pytest.raises(ValueError, match='budget'):
        modelrepair.apply_proposal(source, proposal(slot, 'return;'))


def test_slot_deletion_and_overlap_rules():
    source = 'void f(void) {\nwork();\n}\n'
    slot = next(k for k in edit_slots.slots(source) if k.endswith(':L2'))
    p = proposal(slot, '')
    assert 'work' not in modelrepair.apply_proposal(source, p)
    with pytest.raises(ValueError, match='overlap'):
        modelrepair.apply_proposal(source, modelrepair.Proposal('declarations','test',(p.edits[0],p.edits[0])))


def test_short_slot_is_bound_to_parent_and_code_newlines_are_normalized():
    source = '#include "actor.h"\nvoid f(void) {}\n'
    raw = json.dumps({'kind':'declarations','hypothesis':'define view',
        'edits':[{'slot':'DECLARATIONS','new':r'struct Actor {\nshort timer;\n};\n'}]})
    p = modelrepair.parse_proposal(raw, source=source)
    changed = modelrepair.apply_proposal(source, p)
    assert 'struct Actor {\nshort timer;\n};\n' in changed
    with pytest.raises(ValueError, match='identity changed'):
        modelrepair.apply_proposal(source+'\n', p)
    assert edit_slots.normalize_newlines(r'puts("literal\n");\n') == 'puts("literal\\n");\n'
