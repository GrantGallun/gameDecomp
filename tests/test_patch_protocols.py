import json
import pytest
from solver import patch_protocols as protocols

SOURCE = 'int f(void) {\n    return 1;\n}\n'


def patch(slot='L2', new='    return 2;'):
    return json.dumps({'kind':'expression','hypothesis':'test',
                       'edits':[{'slot':slot,'new':new}]})


def test_slot_only_applies_and_rejects_old_form():
    assert 'return 2' in protocols.apply(SOURCE, patch())
    with pytest.raises(ValueError):
        protocols.apply(SOURCE, '{"kind":"expression","hypothesis":"test","edits":[{"old":"return 1;","new":"return 2;"}]}')


def test_selection_binds_both_source_and_destination():
    selected=protocols.select(SOURCE,'{"slots":["L2"],"reason":"return value"}')
    assert 'return 2' in protocols.apply(SOURCE,patch(),selection=selected)
    with pytest.raises(ValueError,match='stale'):
        protocols.apply(SOURCE+'\n',patch(),selection=selected)
    with pytest.raises(ValueError):
        protocols.apply(SOURCE,patch('L1'),selection=selected)


@pytest.mark.parametrize('slots',[['L999'],['L2','L2'],[],['DECLARATIONS',4]])
def test_bad_selection_declines(slots):
    with pytest.raises(ValueError):
        protocols.select(SOURCE,json.dumps({'slots':slots,'reason':'test'}))


def test_standard_noop_and_escape_gates_remain():
    for p in (patch(new='    return 1;'),patch('DECLARATIONS','#pragma once\n')):
        with pytest.raises(ValueError):
            protocols.apply(SOURCE,p)


def test_focused_context_contains_only_real_slots():
    prompt,allowed=protocols.focused(SOURCE,patch(),'bad edit')
    assert set(allowed)<=set(protocols.locations(SOURCE))
    assert 'L1' in allowed and 'DECLARATIONS' in allowed
    assert SOURCE.splitlines()[0] in prompt


def test_slot_prompt_removes_competing_original_example():
    prompt=protocols.slot_prompt('old example\nTARGET ASSEMBLY (READ-ONLY):\nasm\nCURRENT C')
    assert 'old example' not in prompt
    schema=protocols.patch_schema(protocols.locations(SOURCE))
    assert set(schema['properties']['edits']['items']['properties'])=={'slot','new'}
