"""Recover early spike source only when its pre-run SHA256 verifies exactly."""
import hashlib,json
from pathlib import Path
here=Path(__file__).resolve().parent
frozen=json.loads((here/'portable/preregistration.json').read_text())['code']
code={}
run=(here/'portable/followup/code/run.py').read_text()
code['run.py']=run.replace("        'late_pointer_store_views':adapter.store_views,\n",'').replace(
    'semantic(fn,compiler.target_dump,result.dump,source,ctx)',
    'semantic(fn,assemblies[fn],result.dump,source,ctx)')
byte=(here/'portable/followup/code/byte_address.py').read_text()
start=byte.index('            def store(statement,fmt):'); end=byte.index('        return self',start)
byte=byte[:start]+byte[end:]
byte=byte.replace('BinaryOp, Expression, StoreStmt, StructAccess, Cast, late_unwrap','BinaryOp, Expression')
byte=byte.replace('        self.store_views = []\n','')
byte=byte.replace('evaluate.handle_add_real, evaluate.add_imm, StoreStmt.format','evaluate.handle_add_real, evaluate.add_imm')
byte=byte.replace('evaluate.handle_add_real,evaluate.add_imm,StoreStmt.format','evaluate.handle_add_real,evaluate.add_imm')
byte=byte.replace("return f'({self.type.format(fmt)})({self.expr.format(fmt)})'","return f'(u8 *)({self.expr.format(fmt)})'")
code['byte_address.py']=byte
# Check tuple spelling alternatives. Only a digest match licenses retaining
# a reconstructed snapshot.
for saved in ('self.saved = (evaluate.handle_add_real, evaluate.add_imm)',
              'self.saved = evaluate.handle_add_real,evaluate.add_imm',
              'self.saved = evaluate.handle_add_real, evaluate.add_imm',
              'self.saved = (evaluate.handle_add_real,evaluate.add_imm)'):
    candidate=byte.replace('self.saved = (evaluate.handle_add_real, evaluate.add_imm)',saved)
    if hashlib.sha256(candidate.encode()).hexdigest()==frozen['byte_address.py']:
        code['byte_address.py']=candidate
test=(here/'portable/followup/code/test_byte_address.py').read_text()
for marker in ('def test_byte_address_retains','def test_late_pointer_store','def test_real_sprite'):
    for candidate in (test[:test.index(marker)],test[:test.index(marker)].rstrip()+'\n'):
        if hashlib.sha256(candidate.encode()).hexdigest()==frozen['test_byte_address.py']:
            code['test_byte_address.py']=candidate
for name in ('provenance.py','test_provenance.py'):
    code[name]=(here/'portable/followup/code'/name).read_text()
folder=here/'portable/code'; folder.mkdir(exist_ok=True)
report={}
for name,digest in frozen.items():
    candidates=[code[name].encode(),code[name].replace('\n','\r\n').encode()] if name in code else []
    verified=next((raw for raw in candidates if hashlib.sha256(raw).hexdigest()==digest),None)
    report[name]=verified is not None
    if verified is not None: (folder/name).write_bytes(verified)
(here/'frozen-source-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
