"""Bounded symbol-preserving and byte-loop source-shape hypotheses."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent/'tiny-pilot'
for name in ('osAiGetLength','strlen'):
    folder=ROOT/name
    source=(folder/'source.c').read_text()
    meta=json.loads((folder/'metadata.json').read_text())
    rows=[]
    def add(label,text):
        if text!=source and text not in [r['source'] for r in rows]:
            rows.append(dict(label=label,source=text,parent_source_sha256=meta['source_sha256']))
    if name=='osAiGetLength':
        for typ in ('volatile u32','const u32','volatile const u32'):
            add('symbol-qualifier:'+typ,source.replace('extern u32 AI_LEN_REG;',f'extern {typ} AI_LEN_REG;'))
        for typ,reg in [('u32',''),('u32','register '),('volatile u32',''),('volatile u32','register ')]:
            add('symbol-pointer:'+reg+typ,source.replace('    return AI_LEN_REG;',f'    {reg}{typ} *address = &AI_LEN_REG;\n    return *address;'))
        add('symbol-array',source.replace('extern u32 AI_LEN_REG;','extern u32 AI_LEN_REG[];').replace('return AI_LEN_REG;','return AI_LEN_REG[0];'))
    else:
        prefix=source[:source.index('s32 strlen(')]
        bodies=[
          ('simple-while','u8 *p = arg0; while (*p) p++; return p - arg0;'),
          ('guarded-do','u8 *p = arg0; if (*p) { do { p++; } while (*p); } return p - arg0;'),
          ('preincrement-test','u8 *p = arg0; if (*p) { while (*++p) {} } return p - arg0;'),
          ('simple-for','u8 *p; for (p = arg0; *p; p++) {} return p - arg0;'),
          ('postincrement','u8 *p = arg0; while (*p++) {} return p - arg0 - 1;'),
          ('load-direct-break','u8 *p = arg0; if (*p) { for (;;) { p++; if (!*p) break; } } return p - arg0;'),
          ('load-first','u8 *p = arg0; u8 c = *p; while (c) { p++; c = *p; } return p - arg0;'),
          ('count-loop','u8 *p = arg0; s32 n = 0; while (*p++) n++; return n;'),
        ]
        for label,body in bodies:add(label,prefix+'s32 strlen(u8 *arg0) { '+body+' }\n')
    (folder/'variants-round2.json').write_text(json.dumps(rows,indent=2))
    print(name,len(rows))
