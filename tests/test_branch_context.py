import json
from dataclasses import replace
from types import SimpleNamespace

from solver import branch_context as context, mips_differential as differential


def compare(target, candidate, value=0):
    case = differential.TestCase('path',123,entry_registers=(('a1',value),))
    row = differential.compare_programs(differential.Program.parse('target',target),
        differential.Program.parse('candidate',candidate),case,
        call_arities={'observe':1},return_registers=('v0',))
    return case,row


def test_long_guard_before_write_has_concrete_operand_and_return_context():
    target = 'beqz a1,.Lwrite\nnop\nli v0,0\njr ra\nnop\n.Lwrite:\n' + 'nop\n'*30 + 'li v0,7\nsw v0,0(a0)\njr ra\nnop'
    _,row = compare(target,target.replace('li v0,7','li v0,8'))
    packet = context.packet(row)
    assert row.status == 'failed'
    assert [p['observable'] for p in packet['contexts']] == ['persistent_write','return_or_terminal']
    for anchor in packet['contexts']:
        branch = anchor['target']['decisions'][0]
        assert 'branch taken;' in branch['decision']
        assert branch['operands'][0]['register'] == 'a1'
        assert branch['operands'][0]['value'] == '0x00000000'
        assert 'entry' in branch['operands'][0]['provenance']


def test_missing_call_gets_both_paths_without_ordinal_alignment():
    target = 'move s0,ra\nbeqz a1,.Lcall\nnop\nb .Lend\nnop\n.Lcall:\njal observe\nnop\n.Lend:\nmove ra,s0\nli v0,0\njr ra\nnop'
    _,row = compare(target,target.replace('beqz a1','bnez a1'))
    anchor = context.packet(row)['contexts'][0]
    assert anchor['observable'] == 'call'
    assert anchor['target']['checkpoint_present']
    assert not anchor['candidate']['checkpoint_present']
    assert 'branch taken;' in anchor['target']['decisions'][0]['decision']
    assert 'branch not taken;' in anchor['candidate']['decisions'][0]['decision']


def test_same_reason_different_paths_and_passing_contrast():
    target = 'beqz a1,.Lzero\nnop\nli v0,3\njr ra\nnop\n.Lzero:\nli v0,7\njr ra\nnop'
    bad = target.replace('li v0,3','li v0,4').replace('li v0,7','li v0,8')
    case0,row0 = compare(target,bad,0)
    case1,row1 = compare(target,bad,1)
    assert row0.reasons == row1.reasons
    assert context.failure_key(row0) != context.failure_key(row1)
    _,same_path = compare(target,bad,2)
    assert context.failure_key(row1) == context.failure_key(same_path)
    _,passing = compare(target,target,1)
    contrast = context.passing_alternative(row0,[case0,case1],[row0,passing])
    assert contrast['status'] == 'passed'
    assert contrast['input']['entry_registers'] == (('a1',1),)
    assert context.passing_alternative(row1,[case1],[passing]) is None


def test_indirect_transfer_and_output_bound_keep_full_receipt():
    _,row = compare('li v0,1\njr ra\nnop','li v0,2\njr ra\nnop')
    event = differential.InstructionEvent(0,0,'jr t0',(('t0',0x80000020,'x'*10000),),
                                        effect='indirect control transfer to 0x80000020')
    run = replace(row.target,trace=[replace(event,ordinal=i) for i in range(1000)])
    row = replace(row,target=run)
    packet = context.packet(row)
    history = packet['contexts'][0]['target']
    assert len(json.dumps(packet)) <= 12000
    assert history['omitted_decisions'] >= 994
    assert history['omitted_text_characters'] > 0
    assert history['decisions'][0]['decision'].endswith('0x80000020')
    assert len(run.trace) == 1000
    assert run.trace[0].reads[0][2] == 'x'*10000


def test_prompt_retains_secondary_paths_and_bindings(monkeypatch,tmp_path):
    from solver import modelrepair, compile_obligations
    monkeypatch.setattr(compile_obligations,'header_types',lambda *a,**k:{})
    state = SimpleNamespace(source='int f(void) { return 0; }',semantic={
        'source_sha256':'source-hash','panel_sha256':'panel-hash',
        'feedback':[{'reasons':['first'],'path_context':{'path_sha256':'first-path'}},
                    {'reasons':['second'],'path_context':{'path_sha256':'second-path'},
                     'source_sha256':'source-hash','panel_sha256':'panel-hash'}]})
    prompt = modelrepair.semantic_prompt(tmp_path,'f',state,'jr ra\nnop',{},[])
    assert 'first-path' in prompt and 'second-path' in prompt
    assert 'source-hash' in prompt and 'panel-hash' in prompt
    assert 'branch ordinals are not aligned' in prompt


def test_panel_keeps_distinct_paths_with_unchanged_verdicts(monkeypatch,tmp_path):
    from eval import semantic_lane
    from solver import callee_execution, workspace
    target = 'beqz a1,.Lzero\nnop\nli v0,3\njr ra\nnop\n.Lzero:\nli v0,7\njr ra\nnop'
    bad = target.replace('li v0,3','li v0,4').replace('li v0,7','li v0,8')
    cases = [compare(target,bad,value)[0] for value in (0,1)]
    panel = semantic_lane.Panel.__new__(semantic_lane.Panel)
    panel.cache = {}
    panel.target, panel.function, panel.max_steps = target,'f',100
    panel.cases, panel.arities, panel.returns = cases,{},('v0',)
    panel.call_contracts, panel.debt = {},['finite synthetic inputs']
    panel.callee_environment = callee_execution.Environment({})
    panel.execution_obstructions = []
    panel.stress_execution_debt = False
    panel.identity = 'bound-panel'
    panel.report = {'stress_work':{}}
    obj = tmp_path/'candidate.o'
    obj.with_name('candidate_object_dump_normalized.s').write_text(bad)
    monkeypatch.setattr(workspace,'semantic_assembly',lambda text,obj:text)
    state = SimpleNamespace(source='int f(int x) { return x; }',object_path=obj,
                            attempt=SimpleNamespace(compiled=True,frontend={'passed':True},score=1))
    report = panel(state)
    assert report['counts'] == {'failed':2}
    assert report['authoritative'] is False
    assert len(report['feedback']) == 2
    assert len({f['path_context']['path_sha256'] for f in report['feedback']}) == 2
    assert all(f['panel_sha256'] == report['panel_sha256'] for f in report['feedback'])
    saved = json.loads(obj.with_suffix('.semantic-lane.json').read_text())
    assert saved['feedback'] == json.loads(json.dumps(report['feedback']))
