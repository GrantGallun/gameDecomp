from solver import wide_runtime_interfaces as w


def test_complete_wide_runtime_streams():
    assert w.recognize(w.MULTIPLY)['parameters']==['u64','u64']
    assert w.recognize(w.DIVIDE)['parameters']==['s64','s64']
    renamed=w.DIVIDE.replace('.nonzero','.L123').replace('.result','.L456')
    assert w.recognize(renamed)['argument_words']==4
    decorated='nonmatching __ll_mul, 0x30\nglabel __ll_mul\n'+w.MULTIPLY+'endlabel __ll_mul\n'
    assert w.recognize(decorated)['parameters']==['u64','u64']


def test_changed_operation_word_order_return_or_trap_declines():
    for text in [w.MULTIPLY.replace('dmultu','dmult'),
                 w.MULTIPLY.replace('sw a0,0(sp)','sw a0,4(sp)'),
                 w.DIVIDE.replace('break 6','nop'),
                 w.DIVIDE.replace('dsra32 v0,v0,0','nop'),
                 w.DIVIDE.replace('bnez t7,.nonzero','bnez t7,.result')]:
        assert w.recognize(text) is None


def test_pairs_explicit_word_slots_and_projects_wide_return_high():
    source='''u64 multiply(u64,u64);
void f(void) {
    u64 value;
    value = multiply(/* u64+0x0 */ 0, /* u64+0x4 */ 2, /* u64+0x0 */ 0, /* u64+0x4 */ 3);
    value = multiply(/* u64+0x0 */ value, /* u64+0x4 */ (u32)value, /* u64+0x0 */ 0, /* u64+0x4 */ 4);
}'''
    r,changes=w.pair_arguments(source,[{'name':'multiply',**w.recognize(w.MULTIPLY)}])
    assert len(changes)==2 and 'value >> 32' in r
    assert '/* u64+' not in r and 'u64 multiply(u64,u64);' in r


def test_pairing_rejects_effects_or_missing_annotations():
    rows=[{'name':'divide',**w.recognize(w.DIVIDE)}]
    for source in ['divide(1,2,3,4);','divide(/* s64+0x0 */ side(), /* s64+0x4 */ 2, /* s64+0x0 */ 0, /* s64+0x4 */ 4);']:
        assert w.pair_arguments(source,rows)[0]==source
