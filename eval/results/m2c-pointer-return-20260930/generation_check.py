"""Validate final guards against the retained native compiler source hashes."""
import hashlib,json,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import binary_type_draft as bd

parent=Path('/home/grant/decomp/experiments/m2c-pointer-return-20260930-v1')
repo=Path('/home/grant/decomp/sbk1')
old=json.loads((parent/'comparison.json').read_text())
results=[]
for fn in dict.fromkeys(r['function'] for r in old['rows']):
    with tempfile.TemporaryDirectory(prefix='pointer-return-final-',dir=parent) as directory:
        ws=Path(directory); (ws/'target.s').write_bytes((parent/'targets'/fn/'target.s').read_bytes())
        candidates,reports=bd.variants(repo,fn,ws,pointer_returns=True)
    hashes={hashlib.sha256(source.encode()).hexdigest() for _,source in candidates}
    for row in old['rows']:
        if row['function']==fn: assert row['source_sha256'] in hashes,(fn,row['arm'])
    proposed=any(r.get('label')=='binary-types:pointer-return:valid' and r['status'] in ('generated','duplicate') for r in reports)
    assert proposed==any(r['function']==fn and r['arm']=='pointer-return' for r in old['rows'])
    results.append({'function':fn,'native_source_hashes_reproduced':True,'pointer_return_proposed':proposed,'reports':reports})
output=parent/'final-generation-check.json'
output.write_text(json.dumps({'rows':results,'new_compiles':0,'final_code':{
    name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
    ('solver/m2c_pointer_return.py','solver/binary_type_draft.py','tests/test_m2c_pointer_return.py')}},indent=2)+'\n')
print(json.dumps({'functions':len(results),'compiler_source_hashes_reproduced':len(old['rows']),
    'pointer_return_proposals':sum(r['pointer_return_proposed'] for r in results),'new_compiles':0}))
