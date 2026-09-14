"""Private reconstruction orchestration, using deterministic compiler fixtures."""
import copy
import io
import json
from pathlib import Path
import sqlite3
import urllib.error
from types import SimpleNamespace
from dataclasses import replace

import pytest

from eval import reconstruct as r
from solver import partial_reconstruction as p, workspace, refine
from solver import mips_differential as d

ASM = 'beqz a1,other\nnop\nli v0,1\nb done\nnop\nother:\nli v0,2\ndone:\njr ra\nnop'
SOURCE = 'int f(int x) { return BROKEN; }'


@pytest.fixture
def rig(monkeypatch,tmp_path,request):
    options = getattr(request,'param','')
    missing_dump = isinstance(options,dict) and options.get('missing_normalized_target',False)
    repo = tmp_path/'canonical'
    ws = repo/'nonmatchings'/'f'
    ws.mkdir(parents=True)
    for name,text in [('target.s',ASM),('target_object_dump_normalized.s',ASM),
                      ('target.o','target-object'),('.compiler-target.json','{}')]:
        if missing_dump and name in {'target_object_dump_normalized.s','target.o'}:
            continue
        (ws/name).write_text(text)
    db = tmp_path/'canonical.sqlite'
    with sqlite3.connect(db) as conn:
        refine.ensure_schema(conn)
        conn.execute("INSERT INTO functions(addr,name) VALUES(1,'f')")
        parent = workspace.record_attempt(conn,'f',SOURCE,workspace.Attempt(False,0,False,'','error',''))
    before_db = db.read_bytes()
    before_files = {f.name:f.read_bytes() for f in ws.iterdir()}
    controls = SimpleNamespace(exact=False,full_status='observed_pass',full_calls=0,
                               behavior='normal',panel_identity='fixed-panel',
                               target_assembly=ASM+(options if isinstance(options,str) else ''),
                               scored=[])
    monkeypatch.setattr(r.agentrepair,'_refuse_frozen_heldout',lambda *a:None)
    monkeypatch.setattr(r.frozen_wavefront,'code_paths',lambda *a:[])
    monkeypatch.setattr(workspace,'semantic_assembly',lambda text,obj:text)
    def score(work,repo,tag,source,**kw):
        compiled = 'BROKEN' not in source
        controls.scored.append({'compiled':compiled,'source':source,'strategy':kw.get('strategy')})
        att = workspace.Attempt(compiled,100 if controls.exact else 20,controls.exact,
                                '', '' if compiled else 'fixture compiler error','',frontend={'passed':compiled})
        att.receipt_id = workspace.record_attempt(kw['conn'],kw['func'],source,att,
            run_id=kw['run_id'],run_kind=kw['run_kind'],run_config=kw['run_config'],
            parent_attempt_id=kw.get('parent_attempt_id'),relation=kw.get('relation',''),
            strategy=kw.get('strategy',''),extra=kw.get('extra'))
        (work/(tag+'.o')).write_text(source)
        (work/(tag+'_object_dump_normalized.s')).write_text(source)
        if compiled and not (work/'target_object_dump_normalized.s').exists():
            (work/'target_object_dump_normalized.s').write_text(ASM)
            (work/'target.o').write_text('target-object')
        return att
    monkeypatch.setattr(workspace,'score',score)
    cases = [d.TestCase('nonzero',7,entry_registers=(('a1',1),)),
             d.TestCase('zero',7,entry_registers=(('a1',0),))]
    program = d.Program.parse('f',ASM)
    target_runs = [d.execute_case(program,c,return_registers=('v0',)) for c in cases]
    class Panel:
        def __init__(self,*args,**kwargs):
            self.target,self.identity = controls.target_assembly,controls.panel_identity
            self.cases,self.arities,self.returns,self.max_steps = cases,{},('v0',),100
            self.callee_environment,self.debt = None,['finite fixture cases']
            self.report = {'target_exploration':{}}
        def __call__(self,state):
            controls.full_calls += 1
            return {'status':controls.full_status,'source_sha256':r.sha(state.source),
                    'panel_sha256':self.identity}
    monkeypatch.setattr(r.semantic_lane,'Panel',Panel)
    def suite(target,source,actual_cases,**kw):
        assert target == controls.target_assembly and actual_cases == cases
        rows = []
        for index,run in enumerate(target_runs):
            candidate = copy.deepcopy(run)
            marker = 0 if '__gd_unfinished(0);' in source else (
                1 if '__gd_unfinished(1);' in source and index == 1 else None)
            if marker is not None:
                candidate.calls = [d.CallEvent(0,p.MARKER,(str(marker),),(marker,),'',0)]
            failed = marker is not None or (controls.behavior == 'regress' and index == 0)
            rows.append(d.DifferentialResult(cases[index].name,'failed' if failed else 'passed',
                ('fixture disagreement',) if failed else (), '',run,candidate))
        return rows
    monkeypatch.setattr(d,'run_suite',suite)
    state = r.initialize(repo=repo,db=db,function='f',source=SOURCE,parent_attempt_id=parent,
                         output=tmp_path/'output',work_root=tmp_path/'private',semantic_cases=2)
    return SimpleNamespace(state=state,path=tmp_path/'output/state.json',controls=controls,db=db,
        before_db=before_db,before_files=before_files,canonical_ws=ws,cases=cases,target_runs=target_runs)


def split_proposal(manifest):
    pending = [b['id'] for b in manifest['blocks'] if 5 in b['instructions']]
    return {'manifest_sha256':manifest['manifest_sha256'],'hole_id':0,
        'replacement':'if (x) { return 1; } else { __gd_unfinished(1); }',
        'implemented_blocks':[b['id'] for b in manifest['blocks'] if b['id'] not in pending],
        'child_holes':[{'id':1,'blocks':pending}],'hypothesis':'implement nonzero path'}


def final_proposal(manifest,replacement='return 2;'):
    return {'manifest_sha256':manifest['manifest_sha256'],'hole_id':1,'replacement':replacement,
            'implemented_blocks':manifest['holes'][0]['blocks'],'child_holes':[],
            'hypothesis':'implement remaining zero path'}


def test_initialize_only_private_history_and_no_credit_for_skeleton(rig):
    assert rig.state['status'] == 'compiling_partial'
    version = r.version(rig.state)
    assert version['observations']['counts'] == {'unfinished':2}
    assert not version['complete']
    assert rig.db.read_bytes() == rig.before_db
    assert {f.name:f.read_bytes() for f in rig.canonical_ws.iterdir()} == rig.before_files
    with sqlite3.connect(rig.state['db']) as conn:
        rows = conn.execute('SELECT id,source_code,parent_attempt_id FROM attempts ORDER BY id').fetchall()
    assert len(rows) == 3 and rows[0][1] == rows[1][1] == SOURCE
    assert rows[1][2] == rows[0][0] and rows[2][2] == rows[1][0]


def test_saved_split_then_model_completion_keeps_lineage_and_exact_gate(rig):
    state = r.advance(rig.path,proposal=split_proposal(r.version(rig.state)['manifest']))
    current = r.version(state)
    assert current['observations']['counts'] == {'passed':1,'unfinished':1}
    assert rig.controls.full_calls == 0 and not state['complete']
    proposal = final_proposal(current['manifest'])
    calls = []
    def provider(prompt,settings,schema):
        calls.append((prompt,settings,schema))
        return json.dumps(proposal),{'eval_count':11}
    state = r.advance(rig.path,max_calls=1,provider=provider)
    assert len(calls) == 1 and calls[0][2] == r.SCHEMA
    assert rig.controls.full_calls == 1
    assert not state['complete'] and state['status'] == 'ready_for_full_repair'
    assert not r.version(state)['manifest']['holes']
    with sqlite3.connect(state['db']) as conn:
        proposals = conn.execute('SELECT parent_attempt_id,child_attempt_id,sampling,token_cost FROM model_proposals ORDER BY id').fetchall()
    assert len(proposals) == 2 and all(row[1] for row in proposals)
    assert proposals[1][0] == proposals[0][1]
    assert json.loads(proposals[0][2])['metadata']['new_model_call'] is False
    assert proposals[1][3] == 11
    assert rig.db.read_bytes() == rig.before_db


@pytest.mark.parametrize('mode',['compiler','regress'])
def test_rejected_child_durable_and_previous_passing_parent_retained(rig,mode):
    state = r.advance(rig.path,proposal=split_proposal(r.version(rig.state)['manifest']))
    parent = state['current']
    rig.controls.behavior = mode
    proposal = final_proposal(r.version(state)['manifest'],'BROKEN;' if mode == 'compiler' else 'return 2;')
    state = r.advance(rig.path,proposal=proposal)
    assert state['current'] == parent and len(state['versions']) == 3
    assert state['calls'][-1]['status'] == 'rejected'
    child = r.version(state,2)
    if mode == 'compiler':
        assert not child['attempt']['compiled']
    else:
        assert 'previously passing path regressed' in state['calls'][-1]['reason']
    with sqlite3.connect(state['db']) as conn:
        linked = conn.execute('SELECT child_attempt_id FROM model_proposals ORDER BY id DESC LIMIT 1').fetchone()[0]
    assert linked == child['attempt']['receipt_id']


@pytest.mark.parametrize('exact,semantic,expected',[(False,'observed_pass',False),
    (True,'observed_failure',False),(True,'observed_pass',True)])
def test_completion_requires_no_holes_compiler_exact_and_full_semantic(rig,exact,semantic,expected):
    rig.controls.exact = exact
    rig.controls.full_status = semantic
    state = r.advance(rig.path,proposal=split_proposal(r.version(rig.state)['manifest']))
    assert not state['complete'] and rig.controls.full_calls == 0
    state = r.advance(rig.path,proposal=final_proposal(r.version(state)['manifest']))
    assert state['complete'] is expected and rig.controls.full_calls == 1


def test_target_pending_block_bypassing_candidate_marker_never_earns_credit(rig):
    manifest = p.apply(r.version(rig.state)['manifest'],split_proposal(r.version(rig.state)['manifest']))
    run = rig.target_runs[1]
    row = d.DifferentialResult('zero','passed',(),' ',run,copy.deepcopy(run))
    result = r.classify(manifest,[rig.cases[1]],[row])
    assert result['raw_counts'] == {'passed':1}
    assert result['counts'] == {'unfinished':1} and result['passed_cases'] == []
    assert result['cases'][0]['unfinished_reasons'] == ['target_entered_pending_block']


def test_stale_manifest_and_changed_version_rejected_without_child(rig):
    proposal = split_proposal(r.version(rig.state)['manifest'])
    proposal['manifest_sha256'] = 'stale'
    state = r.advance(rig.path,proposal=proposal)
    assert len(state['versions']) == 1 and state['calls'][-1]['status'] == 'rejected'
    path = Path(state['versions'][0]['path'])
    path.write_text(path.read_text()+' ')
    with pytest.raises(ValueError,match='version changed'):
        r.advance(rig.path,proposal=proposal)


def test_prompt_limits_whole_blocks_and_retains_context():
    asm = '\n'.join(f'block{i}:\nli v0,{i}\nb block{i+1}\nnop' for i in range(40))+'\nblock40:\njr ra\nnop'
    manifest = p.create(SOURCE,'f',asm)
    prompt,shown = r.prompt_for({'manifest':manifest,'observations':{'examples':[{'path_context':'observed guard'}]}},0)
    assert len(shown) <= 24 and len(shown) < len(manifest['blocks'])
    assert 'observed guard' in prompt and 'omitted_blocks' in prompt
    assert manifest['source_sha256'] in prompt and manifest['manifest_sha256'] in prompt


def test_provider_cannot_edit_unselected_hole(rig):
    manifest = r.version(rig.state)['manifest']
    blocks = [b['id'] for b in manifest['blocks']]
    proposal = {'manifest_sha256':manifest['manifest_sha256'],'hole_id':0,
        'replacement':'if (x) { __gd_unfinished(1); } else { __gd_unfinished(2); }',
        'implemented_blocks':[], 'child_holes':[{'id':1,'blocks':blocks[:1]},
        {'id':2,'blocks':blocks[1:]}],'hypothesis':'split two unfinished regions'}
    state = r.advance(rig.path,proposal=proposal)
    manifest = r.version(state)['manifest']
    wrong_hole = next(h for h in manifest['holes'] if h['id'] == 2)
    def provider(*args):
        return json.dumps({'manifest_sha256':manifest['manifest_sha256'],'hole_id':2,
            'replacement':'__gd_unfinished(3);','implemented_blocks':[],
            'child_holes':[{'id':3,'blocks':wrong_hole['blocks']}],'hypothesis':'wrong region'}),{}
    before = len(state['versions'])
    state = r.advance(rig.path,max_calls=1,provider=provider)
    assert len(state['versions']) == before
    assert state['calls'][-1]['status'] == 'rejected'
    assert 'hole' in state['calls'][-1]['error']


def test_completed_status_survives_noop_and_explicit_old_branch_resets_it(rig):
    rig.controls.exact = True
    state = r.advance(rig.path,proposal=split_proposal(r.version(rig.state)['manifest']))
    state = r.advance(rig.path,proposal=final_proposal(r.version(state)['manifest']))
    assert state['complete'] and state['status'] == 'object_exact_observed'
    def unused(*args):
        pytest.fail('completed version must not request a model')
    state = r.advance(rig.path,max_calls=1,provider=unused)
    assert state['complete'] and state['status'] == 'object_exact_observed'
    state = r.advance(rig.path,parent_version=0)
    assert state['current'] == 0 and not state['complete']
    assert state['status'] == 'compiling_partial'


def test_semantic_unavailable_stays_explicitly_unvalidated(rig,monkeypatch):
    monkeypatch.setattr(r,'make_panel',lambda *a:(_ for _ in ()).throw(ValueError('engine unavailable')))
    state = r.advance(rig.path,proposal=split_proposal(r.version(rig.state)['manifest']))
    assert state['status'] == 'compiling_partial_unvalidated' and not state['complete']
    state = r.advance(rig.path,proposal=final_proposal(r.version(state)['manifest']))
    assert state['status'] == 'full_reconstruction_unvalidated' and not state['complete']
    assert r.version(state)['semantic_unavailable'] == 'engine unavailable'


def test_changed_panel_is_rejected_with_raw_proposal_durable(rig):
    rig.controls.panel_identity = 'different-panel'
    proposal = split_proposal(r.version(rig.state)['manifest'])
    r.advance(rig.path,proposal=proposal)
    state = json.loads(rig.path.read_text())
    assert state['current'] == 0 and len(state['versions']) == 1
    assert state['calls'][-1]['status'] == 'evaluation_error'
    assert 'panel changed' in state['calls'][-1]['error']
    with sqlite3.connect(state['db']) as conn:
        saved = conn.execute('SELECT raw_response,parent_attempt_id FROM model_proposals').fetchone()
    assert json.loads(saved[0]) == proposal
    assert saved[1] == r.version(state)['attempt']['receipt_id']


def test_failed_evaluation_keeps_provider_receipt_before_compiler_exception(rig,monkeypatch):
    proposal = split_proposal(r.version(rig.state)['manifest'])
    def broken_score(*args,**kwargs):
        raise OSError('compiler process failed')
    monkeypatch.setattr(workspace,'score',broken_score)
    r.advance(rig.path,max_calls=1,provider=lambda *a:(json.dumps(proposal),{'eval_count':17}))
    state = json.loads(rig.path.read_text())
    assert len(state['calls']) == 1 and state['current'] == 0
    assert state['calls'][-1]['status'] == 'evaluation_error'
    assert 'compiler process failed' in state['calls'][-1]['error']
    with sqlite3.connect(state['db']) as conn:
        raw,tokens,child = conn.execute('SELECT raw_response,token_cost,child_attempt_id FROM model_proposals').fetchone()
    assert json.loads(raw) == proposal and tokens == 17 and child is None


@pytest.mark.parametrize('rig',['\n# MIPS_DIFF_EXTENT observed_global 32\n'],indirect=True)
def test_initialize_manifest_binds_panel_extent_annotations(rig):
    manifest = r.version(rig.state)['manifest']
    assert manifest['target_assembly'] == rig.controls.target_assembly
    assert 'MIPS_DIFF_EXTENT observed_global 32' in manifest['target_assembly']
    assert rig.state['status'] == 'compiling_partial'
    state = r.advance(rig.path,proposal=split_proposal(manifest))
    assert state['calls'][-1]['status'] == 'accepted'


@pytest.mark.parametrize('rig',[{'missing_normalized_target':True}],indirect=True)
def test_never_compiled_original_bootstraps_target_dump_without_path_credit(rig):
    assert [item['compiled'] for item in rig.controls.scored] == [False,True,True]
    assert [item['strategy'] for item in rig.controls.scored] == [
        'partial-original-retained','partial-skeleton-bootstrap','fill-explicit-region']
    assert rig.state['original_attempt']['compiled'] is False
    assert rig.state['bootstrap_attempt']['compiled'] is True
    assert rig.state['status'] == 'compiling_partial' and not rig.state['complete']
    current = r.version(rig.state)
    assert current['attempt']['compiled'] is True
    assert current['manifest']['target_assembly'] == ASM
    assert current['observations']['counts'] == {'unfinished':2}
    assert current['observations']['passed_cases'] == []
    assert rig.controls.full_calls == 0
    assert not (rig.canonical_ws/'target_object_dump_normalized.s').exists()
    assert not (rig.canonical_ws/'target.o').exists()
    assert rig.db.read_bytes() == rig.before_db
    with sqlite3.connect(rig.state['db']) as conn:
        attempts = conn.execute('SELECT id,compiled,parent_attempt_id FROM attempts ORDER BY id').fetchall()
        bootstrap_edge = conn.execute("SELECT relation FROM attempt_edges WHERE child_attempt_id=?",
                                      (rig.state['bootstrap_attempt']['receipt_id'],)).fetchone()
    assert len(attempts) == 4
    assert [row[1] for row in attempts] == [0,0,1,1]
    assert attempts[2][2] == attempts[1][0] == attempts[3][2]
    assert bootstrap_edge == ('skeleton-artifact-bootstrap',)


def guard_manifest():
    asm = ('beqz a0,other\nnop\nli v0,1\nb join\nnop\nother:\nli v0,2\n'
           + '\n'.join(f'join{i if i else ""}:\nb join{i+1}\nnop' for i in range(8))
           + '\njoin8:\njr ra\nnop')
    return p.create(SOURCE, 'f', asm)


def test_entry_guard_controller_owns_all_children_and_only_shows_entry():
    manifest = guard_manifest()
    plan = r.entry_guard_plan(manifest, 0)
    assert plan['roles'] == {'taken': 1, 'fallthrough': 2, 'shared_tail': 3}
    prompt = r.guard_prompt({'manifest': manifest}, plan)
    assert len(plan['entry_instructions']) == 2 and 'li v0,2' not in prompt
    assert 'TAKEN' in prompt and manifest['manifest_sha256'] in prompt
    proposal = r.guard_proposal(plan, {'prefix': '', 'condition': 'x == 0', 'hypothesis': 'entry zero guard'})
    child = p.apply(manifest, proposal)
    assert child['implemented_blocks'] == [0]
    assert child['holes'] == plan['child_holes']
    assert len(child['holes']) == 3
    for case in (d.TestCase('zero', 1, entry_registers=(('a0', 0),)),
                 d.TestCase('nonzero', 1, entry_registers=(('a0', 1),))):
        run = d.execute_case(d.Program.parse('f', manifest['target_assembly']), case)
        observation = p.classify_row(child, {'status': 'passed'}, [e.instruction for e in run.trace], [])
        assert not observation['eligible_for_behavior_credit']


@pytest.mark.parametrize('response', [
    {'prefix': '', 'condition': 'x; return 1', 'hypothesis': ''},
    {'prefix': '__gd_unfinished(99);', 'condition': 'x', 'hypothesis': ''},
    {'prefix': '', 'condition': 'x', 'hypothesis': '', 'child_holes': []},
    {'prefix': '', 'condition': False, 'hypothesis': ''},
    {'prefix': 'return;', 'condition': 'x', 'hypothesis': ''},
    {'prefix': '', 'condition': 'x' * 513, 'hypothesis': ''},
])
def test_entry_guard_rejects_model_ledger_and_expression_injection(response):
    with pytest.raises(ValueError):
        r.guard_proposal(r.entry_guard_plan(guard_manifest(), 0), response)


@pytest.mark.parametrize('change', ['unknown', 'entry_cycle', 'likely', 'small', 'partitioned'])
def test_entry_guard_declines_unsupported_control_shapes(change):
    manifest = guard_manifest()
    if change == 'unknown':
        asm = manifest['target_assembly'].replace('beqz a0,other', 'beqz a0,missing')
    elif change == 'entry_cycle':
        asm = 'start:\n' + manifest['target_assembly'].replace('join8:\njr ra', 'join8:\nb start')
    elif change == 'likely':
        asm = manifest['target_assembly'].replace('beqz a0,other', 'beqzl a0,other')
    elif change == 'small':
        asm = ASM
    else:
        plan = r.entry_guard_plan(manifest, 0)
        manifest = p.apply(manifest, r.guard_proposal(plan, {'prefix': '', 'condition': 'x == 0', 'hypothesis': ''}))
        assert r.entry_guard_plan(manifest, 1) is None
        return
    assert r.entry_guard_plan(p.create(SOURCE, 'f', asm), 0) is None


def test_automatic_guard_provider_raw_receipt_and_normalized_ledger(rig, monkeypatch):
    # Reuse the real persistence fixture, only routing its small diamond through
    # the same guard planner with extra unreachable-free shared blocks.
    plan = r.entry_guard_plan(guard_manifest(), 0)
    parent = r.version(rig.state)
    actual = parent['manifest']
    plan.update(manifest_sha256=actual['manifest_sha256'],
                child_holes=[{'id': 1, 'blocks': [2]}, {'id': 2, 'blocks': [1]}, {'id': 3, 'blocks': [3]}])
    monkeypatch.setattr(r, 'entry_guard_plan', lambda *args: plan)
    raw = {'prefix': '', 'condition': 'x == 0', 'hypothesis': 'zero guard'}
    def provider(prompt, settings, schema):
        assert schema == r.GUARD_SCHEMA and settings['think'] == 'low'
        return json.dumps(raw), {'eval_count': 25}
    state = r.advance(rig.path, max_calls=1, provider=provider)
    assert state['calls'][-1]['status'] == 'accepted'
    assert state['calls'][-1]['proposal_mode'] == 'entry-guard'
    assert state['calls'][-1]['normalized_proposal']['child_holes'] == plan['child_holes']
    assert r.version(state)['observations']['counts'] == {'unfinished': 2}
    assert not state['complete']
    with sqlite3.connect(state['db']) as conn:
        saved = conn.execute('SELECT raw_response, child_attempt_id FROM model_proposals').fetchone()
    assert json.loads(saved[0]) == raw and saved[1] == r.version(state)['attempt']['receipt_id']


def test_bad_request_body_retained_without_repeating_invalid_schema(rig):
    calls = []
    def provider(*args):
        calls.append(1)
        raise urllib.error.HTTPError('http://local/api/chat', 400, 'Bad Request', {},
                                     io.BytesIO(b'{"error":"failed to parse grammar"}'))
    state = r.advance(rig.path, max_calls=2, provider=provider)
    assert len(calls) == 1 and len(state['calls']) == 1
    assert 'failed to parse grammar' in state['calls'][0]['error']
    assert state['current'] == 0 and len(state['versions']) == 1
