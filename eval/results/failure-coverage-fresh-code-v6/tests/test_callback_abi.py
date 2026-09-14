from copy import deepcopy

from solver import callback_abi
from solver import callee_execution, mips_differential as d
import pytest


ASSEMBLY='''lui t0,%hi(root)
lw t0,%lo(root)(t0)
lw a0,0x38(t0)
lw t9,4(a0)
jalr t9
nop
jr ra
nop'''


def measurement():
    return {'global_declarations':[{'name':'root','spelling':'Globals *'}],
        'layouts':{
            'Globals':[{'member':'driver.output','offset':56,'width':4,'pointee':'Filter'}],
            'Filter':[{'member':'handler','offset':4,'width':4,
                       'canonical':'Acmd *(*)(void *, short *, int, int, Acmd *)'}]}}


def test_callback_binding_requires_binary_chain_and_measured_header_slots():
    result=callback_abi.bind(ASSEMBLY,measurement())
    row=result['calls'][0]
    assert row['status']=='bound' and row['contract']['abi']['argument_words']==5
    assert row['contract']['path'][1]['member']=='driver.output'
    assert row['contract']['abi']['return_registers']==['v0']
    assert result['execution_admitted'] is False and result['effects_known'] is False


def test_ambiguous_missing_or_wrong_paths_decline():
    for kind in ('global','union','offset','width','abi'):
        packet=deepcopy(measurement())
        if kind=='global': packet['global_declarations'].append({'name':'root','spelling':'Other *'})
        if kind=='union': packet['layouts']['Filter'].append(dict(packet['layouts']['Filter'][0]))
        if kind=='offset': packet['layouts']['Globals'][0]['offset']=60
        if kind=='width': packet['layouts']['Globals'][0]['width']=8
        if kind=='abi': packet['layouts']['Filter'][0]['canonical']='double (*)(double)'
        row=callback_abi.bind(ASSEMBLY,packet)['calls'][0]
        assert row['status']=='unresolved' and row['decline_reasons']
    assert callback_abi.bind(ASSEMBLY.replace('%lo(root)(t0)','0(sp)'),measurement())['calls'][0]['status']=='unresolved'


def test_closed_word_abi_rejects_unknown_variadic_and_unprototyped():
    assert callback_abi.word_abi('void (*)(void)')['argument_words']==0
    for spelling in ('void (*)()', 'int (*)(int, ...)', 'long long (*)(int)',
                     'int (*)(SomeObject)', 'int (*)(float)', 'void **(*)(void)'):
        assert callback_abi.word_abi(spelling) is None


def test_parameter_record_path_is_hypothesis_not_runtime_binding():
    asm='lw t1,0(a0)\nlw t9,4(t1)\njalr t9\nnop\n'
    m=measurement()
    m['layouts']['Wrapper']=[{'member':'source','offset':0,'width':4,'pointee':'Filter'}]
    assert callback_abi.bind(asm,m)['calls'][0]['status']=='unresolved'
    r=callback_abi.bind(asm,m,parameter_objects={'param0':'Wrapper'})
    assert r['calls'][0]['status']=='hypothesis'
    assert r['calls'][0]['contract']['abi']['argument_words']==5
    assert not callback_abi.CallbackProgram('test',r).contracts
    bad=callback_abi.bind(asm,m,parameter_objects={'param0':'Missing'})
    assert bad['calls'][0]['status']=='unresolved'


def test_parameter_candidates_preserve_signature_disagreement():
    asm='lw t1,0(a0)\nlw t9,4(t1)\njalr t9\nnop\n'
    m=measurement()
    m['layouts']['Wrapper']=[{'member':'source','offset':0,'width':4,'pointee':'Filter'}]
    r=callback_abi.parameter_candidates(asm,m)['parameters'][0]
    assert r['status']=='abi_consensus_hypothesis' and not r['root_type_proven']
    m['layouts']['OtherWrapper']=[{'member':'source','offset':0,'width':4,'pointee':'OtherFilter'}]
    m['layouts']['OtherFilter']=[{'member':'handler','offset':4,'width':4,'canonical':'void (*)(void)'}]
    assert callback_abi.parameter_candidates(asm,m)['parameters'][0]['status']=='ambiguous'


def test_program_bound_runtime_compares_fifth_argument_and_refuses_stale_binding():
    prefix='addiu sp,sp,-40\nsw ra,36(sp)\nli t1,0x10000200\nsw t1,16(sp)\n'
    suffix='lw ra,36(sp)\naddiu sp,sp,40\njr ra\nnop'
    target=prefix+ASSEMBLY.replace('jr ra\nnop',suffix)
    candidate=target.replace('li t1,0x10000200','li t1,0x10000208')
    case=d.TestCase('callback',1,global_writes=(('root',4,d.PLAYER_BASE),),
                    player_writes=((56,4,d.PLAYER_BASE+128),(132,4,0x12345678)))
    raw=d.run_suite(target,candidate,(case,),return_registers=())[0]
    assert raw.status=='passed'
    specs=tuple(callback_abi.admit(body,measurement()) for body in (target,candidate))
    environment=callee_execution.Environment(callbacks=specs)
    checked=d.run_suite(target,candidate,(case,),return_registers=(),callee_environment=environment)[0]
    assert checked.status=='failed'
    call=checked.target.calls[0]
    assert call.arity_known and len(call.raw_arguments)==5 and call.raw_arguments[4]==0x10000200
    assert call.abi_contract['path'][-1]['member']=='handler'
    assert not environment.manifest()['callback_programs'][specs[0].identity]['effects_known']
    stale=d.execute_case(d.Program.parse('changed','nop\n'+target),case,callee_environment=environment)
    assert not stale.calls[0].arity_known
    with pytest.raises(ValueError,match='duplicate callback'):
        callee_execution.Environment(callbacks=(specs[0],specs[0]))
