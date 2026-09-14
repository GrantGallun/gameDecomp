from solver import stack_result_evidence as r
import pytest

SOURCE='void f(void) {\n s32 sp20;\n output(&sp20, x);\n use(sp24);\n}\n'
CALLER='''f:
addiu sp, sp, -64
addiu a0, sp, 32
jal output
nop
lw t0, 36(sp)
jr ra
addiu sp, sp, 64
'''
CALLEE='''output:
sw t0, 0(a0)
sw t1, 4(a0)
jr ra
nop
'''


def test_missing_alias_binds_to_callee_output_and_caller_load():
    result=r.analyse(SOURCE,'f',CALLER,"undeclared identifier 'sp24'",{'output':CALLEE})
    row=result['rows'][0]
    assert row['callee_first_pointer_word_writes']==[0,4]
    assert row['missing_aliases'][0]['relative_offset']==4
    assert row['source_buffer']=='sp20'


def test_missing_or_wrong_base_evidence_declines():
    for caller,callee in [(CALLER.replace('sp, 32','sp, 28'),CALLEE),
                          (CALLER,CALLEE.replace('4(a0)','4(a1)')),
                          (CALLER.replace('36(sp)','40(sp)'),CALLEE)]:
        assert not r.analyse(SOURCE,'f',caller,"undeclared identifier 'sp24'",{'output':callee})['rows']
    assert not r.analyse(SOURCE,'f',CALLER,'',{'output':CALLEE})['rows']


def test_reject_disconnected_uninitialized_result_alias():
    report=r.analyse(SOURCE,'f',CALLER,"undeclared identifier 'sp24'",{'output':CALLEE})
    bad=SOURCE.replace(' s32 sp20;', ' s32 sp20;\n s32 sp24;')
    with pytest.raises(ValueError,match='disconnected stack-result alias'):
        r.validate_candidate(SOURCE,bad,report)
    r.validate_candidate(SOURCE,bad.replace(' use(sp24);',' sp24 = buffer[1];\n use(sp24);'),report)
    r.validate_candidate(SOURCE,bad.replace('output(&sp20','output(&sp24'),report)
    r.validate_candidate(SOURCE+' ',bad,report)  # stale evidence has no veto authority
