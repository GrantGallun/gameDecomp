import importlib


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
