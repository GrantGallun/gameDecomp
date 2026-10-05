"""Audit retained paired compiler receipts and identity of allocated sections."""
from pathlib import Path
import hashlib,json,sqlite3,sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from solver import byte_certificate as cert
P=HERE/'portable'
comparison=json.loads((P/'comparison.json').read_text())
rows=comparison['rows']; assert len(rows)==16
conn=sqlite3.connect((P/'attempts.sqlite').as_uri()+'?mode=ro',uri=True)
attempts={r[0]:r for r in conn.execute('SELECT id,source_sha256,source_code,compiled,exact,parent_attempt_id FROM attempts')}
assert len(attempts)==16
assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==0
assert conn.execute('SELECT COUNT(*) FROM inference').fetchone()[0]==0
conn.close()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
certificates=0
for r in rows:
    work=P/'compiles'/r['function']/r['arm']/'attempt-00001'
    receipt=json.loads((work/'receipt.json').read_text())
    entry=attempts[r['attempt_id']]
    assert sha(work/'source.c')==receipt['source_sha256']==r['source_sha256']==entry[1]
    assert hashlib.sha256(entry[2].encode()).hexdigest()==entry[1]
    assert entry[3:5]==(int(r['compiled']),int(r['exact']))
    target=P/'targets'/r['function']/'target.o'
    assert sha(target)==receipt['target_sha256']==r['target_sha256']
    if r['arm']=='pointer-return':
        baseline=next(b for b in rows if b['function']==r['function'] and b['arm']=='baseline')
        assert entry[5]==baseline['attempt_id']
    else: assert entry[5] is None
    if r['compiled']:
        assert sha(work/'candidate.o')==receipt['verification']['candidate_sha256']
        rerun=cert.certify(target,work/'candidate.o',source=(work/'source.c').read_text())
        assert rerun['exact']==receipt['verification']['exact']==r['exact']==False
        assert receipt['frontend']['passed']==r['frontend_passed']
        assert receipt['frontend']['source_sha256']==r['source_sha256']
        certificates+=1
equal=[]
for r in rows:
    if r['arm']!='pointer-return':continue
    source=P/'compiles'/r['function']/'pointer-return/attempt-00001/candidate.o'
    baseline=P/'compiles'/r['function']/'baseline/attempt-00001/candidate.o'
    assert cert.sections_equivalent(cert.object_image(source.read_bytes())['sections'],
                                   cert.object_image(baseline.read_bytes())['sections'])
    equal.append(r['function'])
for name,digest in json.loads((P/'code-sha256.json').read_text()).items(): assert sha(P/'code'/name)==digest
generation=P/'final-generation-check.json'
if generation.exists():
    check=json.loads(generation.read_text());assert len(check['rows'])==12
    for name,digest in check['final_code'].items(): assert sha(HERE.parents[2]/name)==digest
result={'attempts_audited':16,'certificates_rechecked':certificates,'exact_matches':0,
        'pointer_alternatives_object_equivalent_to_baselines':equal,
        'inference_rows':0,'evidence_rows':0,'final_generation_checked':generation.exists()}
(HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
