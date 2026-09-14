import importlib


def test_census_uses_selected_nodes_not_unrelated_metadata(tmp_path):
    import json
    import pytest
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    path=tmp_path/'census.json'
    path.write_text(json.dumps({'kind':'dag-ordered-differential-pipeline-census',
        'dag':{'nodes':[{'function':'selected'}]},'inventory':[{'function':'notSelected'}]}))
    assert module.historical_exposure([],[path])[0]=={'selected'}
    path.write_text('[]')
    with pytest.raises(ValueError,match='must be an object'):
        module.historical_exposure([],[path])


def test_legacy_jsonl_and_explicit_empty_cluster_are_accounted(tmp_path):
    import json
    import pytest
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    path=tmp_path/'legacy.json'
    records=[{'function':'f','exact':False,'draws':4},{'function':'g','exact':True,'draws':1}]
    path.write_text('\n'.join(json.dumps(row) for row in records))
    assert module.historical_exposure([],[path])[0]=={'f','g'}
    path.write_text(path.read_text()+'\n{"bad":"record"}')
    with pytest.raises(ValueError):
        module.historical_exposure([],[path])
    path.write_text(json.dumps({'kind':'logic-first-connected-dev-cluster','cluster':[]}))
    names,addresses,proof=module.historical_exposure([],[path])
    assert not names and not addresses and proof[0]['selected_functions']==[]


def test_older_wavefront_and_cluster_selections_are_exposure(tmp_path):
    import json
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    paths=[]
    for i,(kind,key) in enumerate((('differential-debugger-wavefront','nodes'),
            ('frozen-wavefront-experiment','nodes'),('logic-first-connected-dev-cluster','cluster'))):
        path=tmp_path/f'{i}.json'
        path.write_text(json.dumps({'kind':kind,key:[{'function':f'selected{i}'}]}))
        paths.append(path)
    assert module.historical_exposure([],paths)[0]=={'selected0','selected1','selected2'}


def test_unfinished_wavefront_configured_target_is_already_exposed(tmp_path):
    import json
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    path=tmp_path/'running.json'
    path.write_text(json.dumps({'kind':'differential-debugger-wavefront','nodes':[],
        'configuration':{'functions':['selectedBeforeFirstNode']}}))
    assert module.historical_exposure([],[path])[0]=={'selectedBeforeFirstNode'}


def test_campaign_history_includes_parked_and_unattempted_nodes(tmp_path):
    import json
    import pytest
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    path=tmp_path/'campaign.json'
    path.write_text(json.dumps({'kind':'resumable-completion-campaign',
        'nodes':{'parked':{'status':'parked'},'pending':{'status':'pending'}}}))
    assert module.historical_exposure([],[path])[0]=={'parked','pending'}
    for nodes in ([],{}, {'bad':None}):
        path.write_text(json.dumps({'kind':'resumable-completion-campaign','nodes':nodes}))
        with pytest.raises(ValueError):
            module.historical_exposure([],[path])


def test_paired_selection_is_disjoint_stable_and_preserves_shapes():
    module = importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    rows = [(f'{domain}_{size}_{leaf}_{i}', size, f'build/src/{domain}/a.o', leaf)
            for domain in ('ultra','game') for size in (32,100,200,800)
            for leaf in (0,1) for i in range(3)]
    batches, counts, shortages = module.paired_selection(rows,'frozen-seed:')
    assert module.paired_selection(list(reversed(rows)),'frozen-seed:') == (batches,counts,shortages)
    assert [len(batch) for batch in batches] == [16,16]
    assert not {r['function'] for r in batches[0]} & {r['function'] for r in batches[1]}
    assert all(count == 3 for count in counts.values()) and not shortages
    assert {r['stratum'] for r in batches[0]} == {r['stratum'] for r in batches[1]}


def test_paired_selection_reports_sparse_strata_without_duplicating_functions():
    module = importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    batches, counts, shortages = module.paired_selection([('only',500,'build/src/ultra/a.o',None)],'seed')
    assert len(batches[0]) == 1 and batches[1] == []
    assert shortages == {'sdk_huge_unknown_shape':1}


def test_larger_strata_are_disjoint_and_balanced():
    module = importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    rows = [(f'f{i}',100,'build/src/game/a.o',0) for i in range(20)]
    batches,counts,shortages = module.paired_selection(rows,'large',4)
    assert [len(batch) for batch in batches] == [4,4]
    assert len({r['function'] for batch in batches for r in batch}) == 8
    assert not shortages


def test_exposure_history_retains_both_names_and_addresses_without_writes(tmp_path):
    import sqlite3
    import hashlib
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    path=tmp_path/'old.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE functions(addr INTEGER,name TEXT); CREATE TABLE attempts(id INTEGER,func_addr INTEGER);'
                          "INSERT INTO functions VALUES(4096,'oldAlias'),(8192,'untouched');"
                          'INSERT INTO attempts VALUES(7,4096),(8,4096);')
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    names,addresses,receipts=module.historical_exposure([path])
    assert names=={'oldAlias'} and addresses=={4096}
    assert receipts[0]['attempt_cutoff']==8 and receipts[0]['exposed_identities']==1
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_selected_parked_target_is_exposed_without_any_attempt(tmp_path):
    import sqlite3
    import json
    import hashlib
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    db=tmp_path/'history.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE functions(addr INTEGER,name TEXT); CREATE TABLE attempts(id INTEGER,func_addr INTEGER);'
                          "INSERT INTO functions VALUES(4096,'parkedAlias'),(8192,'fresh');")
    receipt=tmp_path/'cohort.json'
    receipt.write_text(json.dumps({'kind':'preselected-paired-stratified-development',
        'batches':[[{'function':'parkedAlias'}],[{'function':'notInInventory'}]]}))
    before=hashlib.sha256(receipt.read_bytes()).hexdigest()
    names,addresses,proof=module.historical_exposure([db],[receipt])
    assert names=={'parkedAlias','notInInventory'} and addresses=={4096}
    assert proof[0]['attempt_cutoff']==0
    assert proof[1]['sha256']==before
    assert hashlib.sha256(receipt.read_bytes()).hexdigest()==before


def test_historical_cohort_rejects_unknown_and_malformed_receipts(tmp_path):
    import json
    import pytest
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    path=tmp_path/'cohort.json'
    for payload in ({'kind':'unknown'},
                    {'kind':'preselected-paired-stratified-development','batches':['wrong']},
                    {'kind':'preselected-development-expansion','dev':[{'function':None}]}):
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError):
            module.historical_exposure([],[path])
    path.write_text(json.dumps({'kind':'preselected-development-expansion','dev':[{'function':'selected'}]}))
    assert module.historical_exposure([],[path])[0]=={'selected'}
