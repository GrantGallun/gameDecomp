import hashlib
import json
import sqlite3

import pytest

from eval.campaign_state import Store
from eval.progress_map import MapFeed, project


def test_projection_does_not_treat_similarity_or_finite_semantics_as_exact():
    node = {'status':'pending','size':80,'score':100,'attempt_id':9,
            'residual':{'compiled':True},'semantic_validation':{'status':'observed_pass'}}
    row, detail = project('f', node, 'cluster')
    assert row['category'] == 'partial'
    assert detail['semantic_status'] == 'observed_pass'
    node['residual']['compiled'] = False
    assert project('f', node, 'cluster')[0]['category'] == 'compile_blocked'
    node['status'] = 'object_exact'
    assert project('f', node, 'cluster')[0]['category'] == 'exact'
    assert project('new', {}, 'group')[0]['category'] == 'untouched'
    node['status'] = 'integrated'
    assert project('f', node, 'cluster')[0]['category'] == 'rom_verified'


def test_incremental_snapshot_updates_details_removes_nodes_and_keeps_unknown_size(tmp_path):
    state={'nodes':{'a':{'status':'pending','size':100,'residual':{'compiled':False}},
                    'b':{'status':'object_exact','size':20},'c':{'status':'pending'}},
           'tu_index':{'a':'cluster','b':'cluster'}}
    store=Store(tmp_path/'campaign.json')
    store.save(state)
    feed=MapFeed(tmp_path)
    first=feed.get()
    assert first['total_bytes']==120 and first['exact_bytes']==20
    assert first['unknown_sizes']==1 and first['group_count']==2
    assert feed.get('a')['category']=='compile_blocked'
    state['nodes']['a'].update(status='object_exact')
    del state['nodes']['b']
    state['tu_index']['c']='new-group'
    store.save(state,changed=['a'])
    feed.last=0
    second=feed.get()
    assert second['commit']!=first['commit']
    assert second['total_bytes']==second['exact_bytes']==100
    assert feed.get('c')['group']=='new-group'
    with pytest.raises(KeyError):feed.get('b')


def test_reader_rejects_corrupt_snapshot_and_never_writes_store(tmp_path):
    store=Store(tmp_path/'campaign.json')
    store.save({'nodes':{'f':{'status':'pending','size':4}}})
    before=hashlib.sha256(store.db.read_bytes()).hexdigest()
    feed=MapFeed(tmp_path)
    feed.get()
    assert hashlib.sha256(store.db.read_bytes()).hexdigest()==before
    pointer=json.loads((tmp_path/'campaign.json').read_bytes())
    pointer['sha256']='bad'
    (tmp_path/'campaign.json').write_text(json.dumps(pointer))
    with pytest.raises(ValueError,match='manifest'):
        MapFeed(tmp_path).get()


def test_details_bound_large_reports_and_keep_missing_coverage_explicit():
    node={'residual':{'first_difference':['x'*2000]*100},
          'semantic_validation':{'target_coverage':{'branch_edge_coverage':.25,'missing_instructions':list(range(1000))}},
          'jobs':[{'profile':str(i),'receipt':'private'} for i in range(20)]}
    row, detail=project('f',node,'g')
    assert detail['coverage']=={'branch_edge_coverage':.25}
    assert len(detail['differences'])==16 and max(map(len,detail['differences']))==500
    assert len(detail['recent_work'])==6 and detail['recent_work'][0]['profile']=='19'
    assert row['size'] is None


def test_parked_splits_into_assembly_by_design_and_pipeline_gaps():
    """Fires on both real blocker kinds the campaign records.

    The old single 'parked' bucket put CPU/SDK assembly that stays assembly by
    design next to ordinary C that a pipeline fix unparks -- 8 of 10 long-long
    helpers became exact with main-tree code once the -mips3 recipe existed.
    """
    from eval.progress_map import project
    hardware = {'status': 'parked', 'blocker': {'status': 'hardware_backend_required'}}
    sdk = {'status': 'parked', 'blocker': {'status': 'sdk_control_flow_or_relocation_unsupported'}}
    pipeline = {'status': 'parked', 'blocker': {'status': 'operational_or_intake_failure',
                                                'error': 'ValueError: unsupported compiler ISA/ABI recipe'}}
    backend = {'status': 'parked', 'blocker': {'status': 'object_postprocessing_backend_required'}}
    no_blocker = {'status': 'parked'}
    assert project('a', hardware, 'g')[0]['category'] == 'parked_asm'
    assert project('b', sdk, 'g')[0]['category'] == 'parked_asm'
    assert project('c', pipeline, 'g')[0]['category'] == 'parked'
    assert project('d', backend, 'g')[0]['category'] == 'parked'
    assert project('e', no_blocker, 'g')[0]['category'] == 'parked'
