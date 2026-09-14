"""Retain bounded source hypotheses; never read reference C or copy a DB."""
from pathlib import Path
import hashlib
import json
import sqlite3
from solver import rewrites, principle_variants, hardware_environment

ROOT = Path(__file__).resolve().parent
audit = json.loads((ROOT / 'diff-audit.json').read_text())
db = ROOT.parent / 'resume-pipeline-20260908/campaign.sqlite'
conn = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)
for name in ('osAiGetLength', 'strlen'):
    folder = ROOT / 'tiny-pilot' / name
    folder.mkdir(parents=True, exist_ok=False)
    original = ROOT / 'beyond-diff-inputs' / name
    source = (original / 'candidate.c').read_text()
    target = (original / 'target.s').read_bytes()
    meta = json.loads((original / 'metadata.json').read_text())
    row = conn.execute('SELECT f.addr,f.size,a.source_code,a.source_sha256 FROM functions f JOIN attempts a ON a.func_addr=f.addr WHERE f.name=? AND a.id=?', (name,meta['attempt_id'])).fetchone()
    assert row and row[2] == source and row[3] == hashlib.sha256(source.encode()).hexdigest()
    meta.update(name=name, address=row[0], target_sha256=hashlib.sha256(target).hexdigest())
    (folder / 'metadata.json').write_text(json.dumps(meta,indent=2))
    (folder / 'source.c').write_text(source)
    (folder / 'target.s').write_bytes(target)
    diff = next(r['attempt']['diff_summary'] for r in audit['rows'] if r['function'] == name)
    seen = {source}
    existing = []
    def add(rows,label,code):
        if code not in seen:
            seen.add(code)
            rows.append(dict(label=label,source=code,parent_source_sha256=meta['source_sha256']))
    for r in rewrites.propose(source,diff):
        add(existing,'existing-rewrite:'+r.label,r.apply(source))
    for v in principle_variants.isolated_register_web(source,name,max_variants=8):
        add(existing,'existing-register-web:'+v.label,v.source)
    existing = existing[:8]
    new = []
    if name == 'osAiGetLength':
        view = hardware_environment.register_views(source,name,target.decode())
        (folder / 'register-view-evidence.json').write_text(json.dumps(view,indent=2))
        add(new,'existing-unwired-register-view',view['source'])
        for typ, qualifier in [('u32',''),('u32','register '),('volatile u32',''),('volatile u32','register ')]:
            changed = source.replace('    return AI_LEN_REG;',f'    {qualifier}{typ} *address = ({typ} *)0xA4500004u;\n    return *address;')
            add(new,f'encoded-pointer:{qualifier}{typ}',changed.replace('extern u32 AI_LEN_REG;',''))
        for declaration in ('u32 value;', 'register u32 value;', 'volatile u32 value;'):
            add(new,'encoded-return-temp:'+declaration,view['source'].replace('    return ', '    '+declaration+'\n    value = ').replace(';\n}', ';\n    return value;\n}'))
    else:
        for typ in ('s32','u32','s16','u16','s8','register u8','register u32','volatile u8'):
            add(new,'byte-load-temp:'+typ,source.replace('u8 temp_t7;',typ+' temp_t7;'))
    (folder / 'variants.json').write_text(json.dumps(existing+new,indent=2))
    print(name, 'existing',len(existing),'exploratory',len(new), flush=True)
conn.close()
