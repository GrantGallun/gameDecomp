import hashlib
import pytest
from solver import partial_word_fields as p

SOURCE='void f(Thread *t, void *x) {\n t->unk38 = (s32)x;\n t->unk3C = x;\n}'
ASM='f:\nsw a1,0x38(a0)\nsw a1,0x3c(a0)\njr ra\nnop'
DIAG="no member named 'unk38'\nno member named 'unk3C'"

def measure(source=SOURCE):
    return {'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'layouts':{'Thread':[
        {'member':'context.a0','offset':56,'width':8,'spelling':'u64','owner_size':432}]}}

def test_partial_word_views_preserve_order_and_rhs():
    r=p.propose(SOURCE,'f',ASM,measure(),DIAG)
    assert len(r['changes'])==2
    assert '= (u32)((s32)x);' in r['source'] and '= (u32)(x);' in r['source']
    assert r['source'].index('+ 0x38')<r['source'].index('+ 0x3c')

def test_stale_measurement_rejected():
    with pytest.raises(ValueError,match='source mismatch'):
        p.propose(SOURCE+' ','f',ASM,measure(),DIAG)

@pytest.mark.parametrize('source,asm',[(SOURCE.replace('t->unk38 =','t++; t->unk38 ='),ASM),
    (SOURCE,ASM.replace('sw','sh')),(SOURCE,ASM.replace('0x38','0x40').replace('0x3c','0x44')),
    (SOURCE.replace('t->unk38 = (s32)x;','use(t->unk38);').replace('t->unk3C = x;','use(t->unk3C);'),ASM)])
def test_declines_mutation_wrong_width_offset_or_reads(source,asm):
    assert not p.propose(source,'f',asm,measure(source),DIAG)['changes']
