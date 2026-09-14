from pathlib import Path
from solver.callback_alternatives import candidates, masked_parameter_casts

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'eval/results/last-push-final/createCallbackTaskPreservingArgs.c').read_text()
FUNCTION='createCallbackTaskPreservingArgs'

def test_parameter_mask_cast_changes_prototype_and_definition_together():
    variant=candidates(SOURCE,FUNCTION,1)[0]
    assert variant.source.count('u16 type, s32 priority)')==2
    assert 'type = (u16)type;' in variant.source
    assert 'switch (t8)' in variant.source
    assert 'u8 t8;' in variant.source

def test_refuses_unknown_loop_and_respects_budget():
    assert candidates(SOURCE,'other')==[]
    assert candidates(SOURCE.replace('cur = cur->next;', 'cur = other(cur);'),FUNCTION)==[]
    assert candidates(SOURCE,FUNCTION,0)==[]
    variants=candidates(SOURCE,FUNCTION,7)
    assert len(variants)==7
    assert len({v.source for v in variants})==7
    assert all('do {' not in v.source for v in variants)

def test_generic_balanced_headers_and_unnamed_prototype():
    source='''u32 work(void (*cb)(u32 type), u32);
u32 other(u32 type) { return type; }
u32 work(void (*cb)(u32 type), u32 value) {
    value &= 65535;
    cb(value); return value;
}'''
    changed=masked_parameter_casts(source,'work')[0].source
    assert 'work(void (*cb)(u32 type), u16);' in changed
    assert 'work(void (*cb)(u32 type), u16 value)' in changed
    assert 'other(u32 type)' in changed
    assert 'value = (u16)value;' in changed

def test_parameter_first_use_and_scope_guards():
    assert not masked_parameter_casts('u32 f(u32 x) { use(x); x &= 65535; return x; }','f')
    assert not masked_parameter_casts('u32 f(u32 x) { if(flag) { x &= 65535; } return x; }','f')
    assert not masked_parameter_casts('u32 f(u32 y) { u32 x; x &= 65535; return x; }','f')
    assert not masked_parameter_casts('u32 f(u32 *x) { x &= 65535; return x; }','f')
    assert not masked_parameter_casts('u32 f(u16 x); u32 f(u32 x) { x &= 65535; return x; }','f')
    assert not masked_parameter_casts('u32 f(u32 x) { x &= 255; return x; }','f')
    source='u32 f(u32 x) { x &= 65535; return f(x); }'
    assert 'return f(x);' in masked_parameter_casts(source,'f')[0].source
    assert not masked_parameter_casts(source,'f',0)
