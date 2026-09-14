"""Graft only the verified stack-home generator and its catalog provenance."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def segment(source,node):return ''.join(source.splitlines(keepends=True)[node.lineno-1:node.end_lineno])

if '--refresh' in sys.argv:
    assert (OUT/'staged-manifest.json').is_file() and STAGE.is_dir()
    for name in ('staged-manifest.json','main-tests.log'):
        archive=OUT/(name+'.before-cheap-gate')
        if (OUT/name).exists() and not archive.exists():shutil.copy2(OUT/name,archive)
else:
    shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','results'))
rel='solver/rewrites.py'
source=(ROOT/rel).read_text()
node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='stack_home_padding_rewrites')
prior=(LIVE/rel).read_text()
assert 'def stack_home_padding_rewrites' not in prior
anchor='            + frame_padding_rewrites(code, diff)'
assert prior.count(anchor)==1
changed=prior.replace(anchor,anchor+'\n            + stack_home_padding_rewrites(code, diff)')
(STAGE/rel).write_text(changed+'\n\n'+segment(source,node)+'\n')
rel='patterns/catalog.py'
source=(ROOT/rel).read_text()
entries=[]
for node in ast.parse(source).body:
    if not isinstance(node,ast.Expr) or not isinstance(node.value,ast.Call):continue
    call=node.value
    if not isinstance(call.func,ast.Name) or call.func.id!='register' or len(call.args)!=1:continue
    pattern=call.args[0]
    if isinstance(pattern,ast.Call) and any(k.arg=='id' and isinstance(k.value,ast.Constant) and
            k.value.value=='single-local-stack-home-padding' for k in pattern.keywords):
        entries.append(segment(source,node))
assert len(entries)==1
prior=(LIVE/rel).read_text()
assert 'single-local-stack-home-padding' not in prior
anchor='# ------------------------------------------------------------------ confirmed'
assert prior.count(anchor)==1
(STAGE/rel).write_text(prior.replace(anchor,anchor+'\n\n'+entries[0]))
shutil.copy2(ROOT/'tests/test_stack_home_padding.py',STAGE/'tests/test_stack_home_padding.py')
manifest={}
for rel in ('solver/rewrites.py','patterns/catalog.py','tests/test_stack_home_padding.py'):
    manifest[rel]={'old_sha256':digest(LIVE/rel) if (LIVE/rel).exists() else None,'new_sha256':digest(STAGE/rel)}
(OUT/'staged-manifest.json').write_text(json.dumps(manifest,indent=2))
if '--refresh' not in sys.argv:
    (STAGE/'eval/results').symlink_to(ROOT/'eval/results',target_is_directory=True)
print(json.dumps(manifest,indent=2))
