from solver import stack_buffers, modelrepair, workspace


ASM = '''addiu sp,sp,-64
sw ra,20(sp)
sw zero,56(sp)
addiu a0,sp,24
jal consume
li a1,32
lw ra,20(sp)
addiu sp,sp,64
jr ra
nop'''
SOURCE = '''void f(void) {
    u8 buffer;
    fill(&buffer);
    consume(&buffer,32);
}'''


def test_call_bound_extent_does_not_depend_on_m2c_variable_names():
    rows,report = stack_buffers.candidates(SOURCE,ASM,'f')
    assert len(rows) == 1
    assert 'u8 buffer[32];' in rows[0][1]
    assert 'fill(buffer)' in rows[0][1] and 'consume(buffer,32)' in rows[0][1]
    assert report['plans'][0]['entry_sp_offset'] == -40
    assert report['plans'][0]['next_occupied_entry_sp_offset'] == -8


def test_ambiguous_nonaddress_nested_and_array_uses_decline():
    for source in [SOURCE.replace('fill(&buffer);','buffer=2;'),
                   SOURCE.replace('fill(&buffer);','fill(flag &buffer);'),
                   SOURCE.replace('consume(&buffer,32)','consume(&buffer,foo(32))'),
                   SOURCE.replace('u8 buffer;','u8 buffer[32];'),
                   SOURCE.replace('fill(&buffer);','fill(&buffer); consume(&buffer,32);')]:
        assert not stack_buffers.candidates(source,ASM,'f')[0]
    assert not stack_buffers.candidates(SOURCE,ASM.replace('jal consume','jal other'),'f')[0]


def test_stack_variant_runs_on_compiling_root_with_zero_model_and_true_parent(monkeypatch,tmp_path):
    root = workspace.Attempt(True,70,False,'','','',7,frontend={'passed':True})
    good = workspace.Attempt(True,90,False,'','','',8,frontend={'passed':True})
    seen = []
    monkeypatch.setattr(workspace,'target_asm',lambda *a:ASM)
    def score(*args,**kw):
        seen.append((args[3],kw))
        return good
    monkeypatch.setattr(workspace,'score',score)
    result = modelrepair.search(tmp_path,'f',SOURCE,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0)
    assert result.best_attempt is good and result.calls_attempted == 0
    assert seen[0][1]['parent_attempt_id'] == 7
    assert seen[0][1]['extra']['stack_buffer_hypotheses']['plans']


def test_repeated_consistent_calls_and_byte_pointer_casts_activate():
    asm = ASM.replace('lw ra,20(sp)', 'addiu a0,sp,24\njal consume\nli a1,4\nlw ra,20(sp)')
    source = SOURCE.replace('fill(&buffer);', 'fill((u8 *) &buffer);').replace(
        'consume(&buffer,32);', 'consume(&buffer,32);\n    consume(&buffer,4);')
    rows, report = stack_buffers.candidates(source,asm,'f')
    assert len(rows) == 1 and report['plans'][0]['extent'] == 32
    assert 'fill((u8 *) buffer);' in rows[0][1]
    assert '&buffer' not in rows[0][1]
    assert not stack_buffers.candidates(source,asm.replace('jal consume\nli a1,4',
        'addiu a0,sp,28\njal consume\nli a1,4'),'f')[0]
    assert not stack_buffers.candidates(source.replace('consume(&buffer,4)',
        'consume(&other,4)'),asm,'f')[0]
