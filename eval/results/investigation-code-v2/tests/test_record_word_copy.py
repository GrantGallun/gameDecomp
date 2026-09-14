import hashlib
from copy import deepcopy
from solver.record_word_copy import propose

SOURCE='''void f(void) {
    table->records[index.value].first = M2C_UNALIGNED32(record);
}'''
ASM='''lui a0,%hi(table)
addiu a0,a0,%lo(table)
lui v0,%hi(index)
lh v0,%lo(index)(v0)
lui t7,%hi(record)
addiu t7,t7,%lo(record)
lw at,0(t7)
sll t6,v0,2
addu t9,a0,t6
swl at,20(t9)
swr at,23(t9)
'''


def measurement():
    return {'kind':'target-compiler-header-layouts',
        'source_sha256':hashlib.sha256(SOURCE.encode()).hexdigest(),
        'global_declarations':[{'name':n,'canonical':t} for n,t in
                               [('table','Table[1]'),('index','Index'),('record','Record')]],
        'layouts':{'Table':[{'member':'records','canonical':'Record[11]','array':True,'offset':20,'width':44}],
                   'Record':[{'member':'first','offset':0,'width':1,'owner_size':4}],
                   'Index':[{'member':'value','offset':0,'width':2,'spelling':'s16'}]}}


def test_measured_record_and_binary_word_copy():
    r=propose(SOURCE,'f',ASM,measurement())
    assert '(u8 *)&(table->records[index.value])' in r['source']
    assert '((u8 *)&record)[3]' in r['source']
    assert r['source'].count('_record_copy_0_dst[')==4
    assert r['changes'][0]['record_size']==4


def test_wrong_layout_width_stride_or_source_declines():
    for asm in [ASM.replace('sll t6,v0,2','sll t6,v0,1'),ASM.replace('23(t9)','24(t9)'),
                ASM.replace('%hi(record)','%hi(other)'),ASM.replace('lw at,0','lw at,4'),
                ASM.replace('addu t9,a0,t6','addu t9,a1,t6')]:
        assert not propose(SOURCE,'f',asm,measurement())['changes']
    for key,value in [('owner_size',8),('offset',1),('width',2)]:
        m=deepcopy(measurement());m['layouts']['Record'][0][key]=value
        assert not propose(SOURCE,'f',ASM,m)['changes']


def test_stale_measurement_and_pointer_root_rejected():
    m=measurement();m['source_sha256']='stale'
    assert not propose(SOURCE,'f',ASM,m)['changes']


def test_repeated_compatible_externs_but_not_conflicting_types():
    m=measurement();m['global_declarations'].append({'name':'table','canonical':'Table[1]'})
    assert propose(SOURCE,'f',ASM,m)['changes']
    m['global_declarations'].append({'name':'table','canonical':'Other'})
    assert not propose(SOURCE,'f',ASM,m)['changes']
    m=measurement();m['global_declarations'][0]['canonical']='Table *'
    assert not propose(SOURCE,'f',ASM,m)['changes']
