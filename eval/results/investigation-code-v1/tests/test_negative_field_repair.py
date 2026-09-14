from solver import negative_field_repair as r


def test_float_array_pointer_negative_byte_offset():
    s='void f(void) {\n f32 (*p)[4];\n p->unk-4 = value;\n}'
    report=r.propose(s,'f','swc1 f0,-4(v0)','member reference base type')
    assert '(*(f32 *)((unsigned char *)p - 0x4)) = value' in report['source']


def test_reject_unobserved_width_and_kind():
    s='void f(void) {\n f32 *p;\n p->unk-C = value;\n}'
    for asm in ('swc1 f0,-4(v0)','sh t0,-12(v0)','sw t0,-12(v0)','swc1 f0,-12(sp)'):
        assert not r.propose(s,'f',asm,'member reference base type')['changes']


def test_structs_parameters_comments_and_missing_diagnostics_decline():
    for s in ('void f(void) {\n R *p;\n p->unk-4 = value;\n}',
              'void f(f32 *p) {\n p->unk-4 = value;\n}',
              'void f(void) {\n f32 *p;\n /* p->unk-4 = value; */\n}'):
        assert not r.propose(s,'f','swc1 f0,-4(v0)','member reference base type')['changes']
    assert not r.propose('void f(void) {}','f','','')['changes']


def test_shadowed_scalar_declines():
    s='void f(void) {\n f32 *p;\n {\n int p;\n p->unk-4 = value;\n }\n}'
    assert not r.propose(s,'f','swc1 f0,-4(v0)','member reference base type')['changes']
