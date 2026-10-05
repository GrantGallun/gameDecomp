"""Finite address alternatives preserve ordinary C and expose related uses."""
import importlib.util
import pytest

from solver.m2c_uncertainty import sha


def module():
    assert importlib.util.find_spec('solver.m2c_address_choices'), 'address choices are missing'
    from solver import m2c_address_choices
    return m2c_address_choices


def report(source, expression='(p + (i * 4))'):
    return {'source_sha256':sha(source),'hazards':[{
        'kind':'unrecovered-address-add','target_size_hypothesis':4,
        'c_expressions':[expression],'instruction':{'synthetic':False,
        'byte_attribution_status':'verified-target-word','address':'80001008',
        'word':'00854021','mnemonic':'addu','instruction':'addu $t0, $a0, $a1'}}]}


SOURCE='typedef int s32; void sink(s32 *);\nvoid f(s32 *p, s32 i) {\n    s32 *q;\n    q = (p + (i * 4));\n    sink(q);\n}\n'


def test_positive_byte_and_array_choices_with_destination_and_call_uses():
    menu=module().build(SOURCE,report(SOURCE),function='f')
    assert [c['kind'] for c in menu['choices']]==['byte-view','array-view']
    byte,array=menu['choices']
    assert '(unsigned char *)(p)' in byte['new']
    assert '(s32 *)' in byte['new'] and array['new']=='(p + (i))'
    uses=byte['dependencies']
    assert {'p','i','q'}<=set(uses['identifiers'])
    assert any('sink(q)' in r['excerpt'] for r in uses['occurrences'])
    assert any('void sink(s32 *)' in p for p in uses['callee_declarations'])
    assert uses['scope']=='lexical uses; no CFG, alias or complete ownership proof'
    candidate=SOURCE.replace(byte['old'],byte['new'])
    assert module().validate(SOURCE,candidate,menu)==byte['id']


def test_struct_view_preserves_current_type_without_inventing_array_layout():
    source=SOURCE.replace('s32 *p','struct S *p')
    menu=module().build(source,report(source),function='f')
    assert len(menu['choices'])==1
    assert '(struct S *)' in menu['choices'][0]['new']


@pytest.mark.parametrize('mutation', ['stale','unverified','synthetic','side-effect','repeated'])
def test_unknown_or_ambiguous_inputs_decline(mutation):
    source=SOURCE; evidence=report(source)
    if mutation=='stale':evidence['source_sha256']='wrong'
    elif mutation=='unverified':evidence['hazards'][0]['instruction']['byte_attribution_status']='annotated-unverified'
    elif mutation=='synthetic':evidence['hazards'][0]['instruction']['synthetic']=True
    elif mutation=='side-effect':
        source=SOURCE.replace('(i * 4)','(i++ * 4)');evidence=report(source,'(p + (i++ * 4))')
    elif mutation=='repeated':
        source=SOURCE.replace('sink(q);','q = (p + (i * 4)); sink(q);');evidence=report(source)
    menu=module().build(source,evidence,function='f')
    assert not menu['choices'] and menu['declines']


def test_menu_rejects_unrelated_edits_and_stale_parents():
    menu=module().build(SOURCE,report(SOURCE),function='f')
    with pytest.raises(ValueError,match='outside'):
        module().validate(SOURCE,SOURCE.replace('sink(q)','sink(p)'),menu)
    with pytest.raises(ValueError,match='stale'):
        module().validate(SOURCE+' ',SOURCE,menu)


def test_menu_cap_is_visible_and_comments_do_not_create_ambiguity():
    source=SOURCE+'/* (p + (i * 4)) */\n'
    menu=module().build(source,report(source),function='f',max_choices=1)
    assert len(menu['choices'])==1 and menu['omitted_choices']==1
    assert menu['status']=='partial'


def test_nondivisible_offsets_keep_byte_view_only():
    source=SOURCE.replace('(i * 4)','(i * 2)')
    menu=module().build(source,report(source,'(p + (i * 2))'),function='f')
    assert [c['kind'] for c in menu['choices']]==['byte-view']
