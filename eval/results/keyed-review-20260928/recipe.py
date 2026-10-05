from pathlib import Path
import sys,json
root=Path('/mnt/c/Code/gameDecomp'); sys.path.insert(0,str(root/'eval/results/resume-pipeline-20260908/code'))
from solver import ido_stages
repo=Path('/home/grant/decomp/sbk1');ws=repo/'nonmatchings/configureRaceViewport'
print(json.dumps(ido_stages._recipe_command(repo,ws,'configureRaceViewport')))
a=(repo/'Makefile').read_text().splitlines()
print('\n'.join(f'{n+1}: {a[n]}' for n in range(78,131)))
print('ASSERT_HEADERS',list((repo/'include').rglob('assert.h')))
print('TRACE_SUBDIRS',list((root/'tools/ido-trace').glob('*')))
