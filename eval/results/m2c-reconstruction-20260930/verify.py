"""Audit all retained compiler receipts, attempts, certificates and observer exports."""
import hashlib, json, sqlite3, sys
from pathlib import Path
here=Path(__file__).resolve().parent
sys.path.insert(0,str(here.parents[2]))
from solver import byte_certificate as certificate

sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
bundles=[(here/'portable',48),(here/'portable/followup',12),(here/'portable/initial-failure',1),
         (here/'joint/portable',12),(here/'joint/portable/secondary',8)]
audited=certs=0
for bundle,count in bundles:
    receipts=list((bundle/'compiles').rglob('receipt.json'))
    assert len(receipts)==count,(bundle,len(receipts))
    conn=sqlite3.connect((bundle/'attempts.sqlite').as_uri()+'?mode=ro',uri=True)
    attempts=list(conn.execute('SELECT id,source_sha256,source_code,compiled,exact,parent_attempt_id FROM attempts'))
    expected=60 if bundle.name=='followup' else count
    assert len(attempts)==expected
    assert conn.execute('SELECT COUNT(*) FROM inference').fetchone()[0]==0
    assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==0
    if bundle.name=='followup':
        assert {r[0] for r in attempts if r[0]>48}==set(range(49,61))
        assert all(r[5] in {1,5,9,13,17,21,25,29,33,37,41,45} for r in attempts if r[0]>48)
    targets={sha(p):p for p in (bundle/'targets').rglob('target.o')}
    matched=set()
    for path in receipts:
        receipt=json.loads(path.read_text()); work=path.parent; source=work/'source.c'
        assert sha(source)==receipt['source_sha256']
        rows=[r for r in attempts if r[1]==receipt['source_sha256'] and r[0] not in matched
              and (bundle.name!='followup' or r[0]>48)]
        assert rows
        entry=rows[0]; matched.add(entry[0])
        assert hashlib.sha256(entry[2].encode()).hexdigest()==receipt['source_sha256']
        assert entry[3:5]==(int(receipt['compiled']),int(receipt['exact']))
        assert receipt['target_sha256'] in targets
        if receipt['compiled']:
            candidate=work/'candidate.o'; target=targets[receipt['target_sha256']]
            cert=receipt['verification']
            assert cert['source_sha256']==sha(source)
            assert cert['target_sha256']==sha(target)
            assert cert['candidate_sha256']==sha(candidate)
            measured=certificate.certify(target,candidate,source=source.read_text())
            assert measured['exact']==cert['exact']==receipt['exact']==False
            assert receipt['frontend']['source_sha256']==sha(source)
            certs+=1
        audited+=1
    assert len(matched)==count
    conn.close()
selection=json.loads((here/'portable/selection.json').read_text())
census=json.loads((here.parent/'joint-reconstruction-20260930/census.json').read_text())
assert not set(selection['functions']) & set(census['heldout'])
assert selection==json.loads((here/'portable/followup/selection.json').read_text())
frozen=json.loads((here/'portable/followup/preregistration.json').read_text())
for name,expected in frozen['code'].items(): assert sha(here/'portable/followup/code'/name)==expected
early=json.loads((here/'portable/preregistration.json').read_text())['code']
for p in (here/'portable/code').glob('*.py'): assert sha(p)==early[p.name]
unchanged=[]
for fn in selection['functions']:
    source_a=here/'portable/compiles'/fn/'baseline/attempt-00001/source.c'
    source_b=here/'portable/followup/compiles'/fn/'byte-store-view/attempt-00001/source.c'
    if sha(source_a)==sha(source_b):
        object_a=source_a.parent/'candidate.o'; object_b=source_b.parent/'candidate.o'
        assert certificate.sections_equivalent(certificate.object_image(object_a.read_bytes())['sections'],
            certificate.object_image(object_b.read_bytes())['sections'])
        unchanged.append(fn)
assert len(unchanged)==10
observer=json.loads((here/'observer-verification.json').read_text())
annotations=0
for row in observer['rows']:
    assert row['stdout_identical'] and not row['observation_errors'] and not row['truncation']
    report=json.loads((here/'observed'/(row['function']+'.json')).read_text())
    assert report['training_eligible']==False and report['origin_coverage_complete']==False
    for function in report['functions']:
        assert not function['truncation']
        instructions={i['id']:i for i in function['instructions']}
        nodes={n['id']:n for n in function['expressions']}
        for node in nodes.values():
            assert node['type_hypothesis']['status']=='inferred_mutable'
            assert all(e['expression'] in nodes for e in node['children'])
            assert all(i in instructions for i in node['register_write_witnesses'])
        for item in instructions.values():
            if item['source_address']:
                assert item['source_word'] in item['source_line']
                assert item['source_sha256']==sha(here/'portable/followup/drafts'/row['function']/'byte-store-view/input.s')
                annotations+=1
result={'compiler_attempts_audited':audited,'certificates_rechecked':certs,'exact_matches':0,
    'new_followup_attempts':12,'duplicate_parent_rows_not_counted':48,
    'unchanged_source_and_object_controls':unchanged,'observer_functions':len(observer['rows']),
    'observer_expression_nodes':sum(r['expressions'] for r in observer['rows']),
    'source_address_word_annotations':annotations,'heldout_overlap':[],
    'inference_rows':0,'evidence_rows':0,'followup_code_snapshots_verified':True}
(here/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
