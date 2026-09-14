from solver import dataflow


def test_stack_call_words_include_delay_slot_store():
    flow=dataflow.analyse('''addiu sp, sp, -32
lw t0, 48(sp)
jalr t9
sw t0, 16(sp)
''')
    call=next(iter(flow.callsites.values()))
    assert call.stack_arguments[0]==dataflow.Value.loaded(dataflow.Value.address('stack',16),width=4)
    assert call.stack_arguments[1:]==(None,None,None)


def test_stack_call_words_unknown_sp_and_overwrite():
    for asm in ['''addiu sp, sp, -32
sw a0, 16(sp)
mfc1 t0, f8
jalr t9
sw t0, 16(sp)
''','''move sp, t0
jalr t9
nop
''']:
        call=next(iter(dataflow.analyse(asm).callsites.values()))
        assert call.stack_arguments==(None,)*4


def test_header_binding_reports_potential_words_without_claiming_arity():
    from solver.callback_abi import bind
    r=bind('jalr t9\nsw a0,16(sp)\n',{'layouts':{}})
    row=r['calls'][0]
    assert row['status']=='unresolved'
    assert row['potential_stack_argument_values'][0]['name']=='param0'
