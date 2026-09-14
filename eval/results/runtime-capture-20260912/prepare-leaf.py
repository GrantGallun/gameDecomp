import hashlib
import json
from pathlib import Path
import re
import shutil
import yaml

root=Path('eval/results/runtime-capture-20260912')
repo=Path('/home/grant/decomp/sbk1')
name='getRelocatableHeapBlockBase'
config=yaml.safe_load((repo/'snowboardkids.yaml').read_text())
rom=repo/config['options']['target_path']
raw=rom.read_bytes()
assert hashlib.sha1(raw).hexdigest()==config['sha1']
shutil.copyfile(rom,root/'pilot-rom.z64')
symbols={m[1]:int(m[2],16) for m in re.finditer(r'(?m)^\s*(\w+)\s*=\s*(0x[0-9a-fA-F]+)\s*;', (repo/'symbol_addrs.txt').read_text())}
target=repo/'nonmatchings'/name/'target.s'
if not target.exists():
    found=list((repo/'asm').rglob(name+'.s'))
    assert len(found)==1,found
    target=found[0]
text=target.read_text()
print(json.dumps({'rom':str(rom),'header':raw[:64].hex(),'target':str(target),'assembly':text,
    'symbols':{k:v for k,v in symbols.items() if k in {name,'gRelocatableHeapBlockStartAliases'}}},indent=2))
(root/'target.s').write_text(text)
(root/'rom-identity.json').write_text(json.dumps({'rom_sha256':hashlib.sha256(raw).hexdigest(),'original_rom':str(rom),'source_target':str(target),'symbols':{k:v for k,v in symbols.items() if k in {name,'gRelocatableHeapBlockStartAliases'}}},indent=2))
