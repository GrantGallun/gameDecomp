from pathlib import Path
import json,hashlib,sys
root=Path('/mnt/c/Code/gameDecomp')
rev=root/'eval/results/resume-pipeline-20260908/revisions/20260928-regalloc-keyed'
state=json.loads((rev/'applied.json').read_text()) if (rev/'applied.json').exists() else None
print('RECEIPTS', [p.name for p in rev.glob('*.json')])
frozen=rev.parents[1]/'code'
for rel in ['solver/regalloc_search.py','solver/ido_stages.py','solver/compiler_experiment.py','eval/agentrepair.py']:
 print('HASH',rel,[(str(p),hashlib.sha256(p.read_bytes()).hexdigest()) for p in [rev/'staged'/rel,frozen/rel]])
repo=Path('/home/grant/decomp/sbk1')
print('COMPILERS',list((repo/'tools/ido-recomp/linux').glob('*')))
print('IDENTITY',(repo/'nonmatchings/configureRaceViewport/.compiler-target.json').read_text())
for n,line in enumerate((repo/'Makefile').read_text().splitlines(),1):
 if any(x in line for x in ['IDO','OPT_FLAGS','CFLAGS','NDEBUG','GLEVEL']):print(n,line)
print('IDO_DIRS',[str(p) for p in Path('/home/grant/decomp').glob('*ido*')])
