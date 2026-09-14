from solver import literal_call_repair as r
from solver import dataflow

SOURCE='? notify(?, ?);\nvoid f(void) {\n    notify(0x47, 0x32);\n}\n'
ASM='f:\nli a0, 0x47\njal notify\nli a1, 0x32\njr ra\nnop\n'


def test_discarded_literal_word_call():
    result=r.propose(SOURCE,'f',ASM)
    assert result['changes']
    assert 'void notify(s32, s32);' in result['source']


def test_no_guessed_values_or_used_return_or_other_function():
    for source in [SOURCE.replace('notify(0x47','result = notify(0x47'),
                   SOURCE.replace('0x47','x'), SOURCE+'void g(void) { notify(1,2); }']:
        assert not r.propose(source,'f',ASM)['changes']
    assert not r.propose(SOURCE,'f',ASM.replace('0x32','0x33'))['changes']


def test_unresolved_switch_arm_requires_local_constants():
    asm='f:\njr t0\nnop\narm:\nli a0, 0x47\njal notify\nli a1, 0x32\njr ra\nnop\n'
    result=r.propose(SOURCE,'f',asm)
    assert result['changes'][0]['call_instructions']==[3]
    assert not r.propose(SOURCE,'f',asm.replace('li a0, 0x47','move a0, s0'))['changes']


def test_zero_register_constants_and_predecessor_delay_slot():
    asm='f:\njr t0\nnop\narm:\nbnez s0, exit\naddiu a0, zero, 0x47\njal notify\naddiu a1, zero, 0x32\nexit:\njr ra\nnop\n'
    assert r.propose(SOURCE,'f',asm)['changes']
    flow=dataflow.analyse('f:\naddiu a0, zero, 7\njal notify\nnop\njr ra\nnop\n')
    assert next(iter(flow.callsites.values())).arguments[0]==dataflow.Value.constant(7)
