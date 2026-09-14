"""Archive private pilot receipts; never reads or copies campaign state."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

root=Path(__file__).resolve().parent/'tiny-pilot'
native=Path('/home/grant/decomp/tiny-pilot-20260913')
parser=argparse.ArgumentParser()
parser.add_argument('--runs',nargs='+',default=['osAiGetLength-r1','osAiGetLength-r2',
    'osAiGetLength-fixed-view','strlen-r1','strlen-r2','strlen-final-generator'])
for name in parser.parse_args().runs:
    if '/' in name or '\\' in name or name in {'.','..'}:
        raise ValueError('expected private run basename')
    source=native/name
    out=root/'receipts'/name
    out.mkdir(parents=True,exist_ok=False)
    for file in source.iterdir():
        if file.is_file():shutil.copy2(file,out/file.name)
    shutil.copytree(source/'inputs',out/'inputs')
    objects=list((source/'repo'/'nonmatchings').glob('**/*.o'))
    for obj in objects:
        relative=obj.relative_to(source/'repo')
        dest=out/'objects'/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(obj,dest)
    manifest={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'archive-manifest.json').write_text(json.dumps(manifest,indent=2))
    report=json.loads((out/'summary.json').read_text())
    print(name,json.dumps(report.get('totals')))
