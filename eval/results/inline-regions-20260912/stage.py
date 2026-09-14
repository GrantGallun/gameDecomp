"""Scoped inlining deployment over frozen code; never wholesale main upgrade."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

if '--refresh' not in sys.argv:
    shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','results'))
    (STAGE/'eval/results').symlink_to(ROOT/'eval/results',target_is_directory=True)
manifest={}
for rel in ('solver/inline_regions.py','solver/inline_expansion.py','eval/inline_regions.py',
            'tests/test_inline_regions.py','tests/test_inline_expansion.py','tests/test_inline_regions_audit.py'):
    shutil.copy2(ROOT/rel,STAGE/rel)
    manifest[rel]={'old_sha256':sha(LIVE/rel),'new_sha256':sha(STAGE/rel)}

def graft(rel, changes):
    text=(LIVE/rel).read_text()
    for before,after in changes:
        assert text.count(before)==1,(rel,before)
        text=text.replace(before,after,1)
    compile(text,rel,'exec')
    (STAGE/rel).write_text(text)
    manifest[rel]={'old_sha256':sha(LIVE/rel),'new_sha256':sha(STAGE/rel),
                   'scope':'Only bounded inlining machinery; preserve other frozen behavior'}

graft('solver/modelrepair.py',[
    ('    from solver import edit_slots, type_transaction as transaction\n',
     '    from solver import edit_slots, inline_regions, type_transaction as transaction\n'
     '    diagnosis_block += inline_regions.prompt(asm)\n'),
    ('    from solver import compile_obligations, edit_slots\n',
     '    from solver import compile_obligations, edit_slots, inline_regions\n'),
    ("    prompt = prefix+json.dumps(semantic_packet(packet), separators=(',', ':'))+suffix\n",
     "    suffix += inline_regions.prompt(asm)\n"
     "    prompt = prefix+json.dumps(semantic_packet(packet), separators=(',', ':'))+suffix\n")])
graft('solver/code_shapes.py',[
    ('    def unique():\n        seen = {source}\n',
     '    def unique():\n        from solver import inline_expansion\n        seen = {source}\n'),
    ('        for batch in zip_longest(loops(), branches(), expressions()):\n',
     '        for batch in zip_longest(loops(), branches(), expressions(),\n'
     '                                inline_expansion.candidates(source, function)):\n')])
(OUT/'staged-manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({'files':len(manifest),'stage':str(STAGE)}))
