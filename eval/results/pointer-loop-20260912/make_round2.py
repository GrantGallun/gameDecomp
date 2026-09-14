"""Follow the best measured residual: loop lowering, load width and stack homes."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from solver import code_shapes

OUT=Path(__file__).resolve().parent
selection=OUT/'selection/traversal-pool/drawCharacterSelectCoursePreviewFrame'
metadata=json.loads((selection/'metadata.json').read_bytes())
rows=json.loads((OUT/'variants-round1.json').read_bytes())
parent=next(row for row in rows if row['label']=='units=bytes;walk=bytes;stack=both;reuse=0')['source']
parent_hash=hashlib.sha256(parent.encode()).hexdigest()

def replace(source,old,new):
    assert source.count(old)==1,old
    return source.replace(old,new,1)

def loops(source,kind):
    if kind=='original': return source
    for ordinal,(counter,limit) in enumerate((('var_s1','0x10'),('var_s2','0x80'))):
        start=source.index('    for (;;) {',source.index(f'    {counter} = 0;'))
        opening=source.index('{',start)
        end=code_shapes._close(code_shapes._mask(source),opening,'{','}')
        body=source[opening+1:end]
        body=replace(body,f'        if (!({counter} < {limit})) break;','')
        if kind=='do':
            new='    do {'+body+f'    }} while ({counter} < {limit});'
        elif kind=='goto':
            label=f'gd_pointer_loop_{ordinal}'
            new=label+':\n'+body+f'    if ({counter} < {limit}) goto {label};'
        elif kind=='for':
            # Explicit source fixed start and bounds establish at least one
            # iteration; there are no remaining breaks/continues in these loops.
            new=f'    for (; {counter} < {limit};) {{'+body+'    }'
        else: raise ValueError(kind)
        source=source[:start]+new+source[end+1:]
    return source

variants=[]
for kind in ('original','do','goto','for'):
    for placement,amount in [('none',0),('front',4),('front',8),('front',12),('front',16),('between',4),('between',8),('after',8)]:
        source=loops(parent,kind)
        # Header declares a signed halfword; target explicitly uses lhu. This
        # read preserves the same16 stored bits and does not change any header.
        source=replace(source,'(u16) arg0->sprite.index','*(u16 *)&arg0->sprite.index')
        pad=f'    volatile unsigned char gd_pointer_pad[{amount}];\n'
        if placement=='front':
            source=replace(source,'    volatile u16 sp4E;\n',pad+'    volatile u16 sp4E;\n')
        elif placement=='between':
            source=replace(source,'    u16 *volatile sp44;\n',pad+'    u16 *volatile sp44;\n')
        elif placement=='after':
            source=replace(source,'    u16 *volatile sp44;\n','    u16 *volatile sp44;\n'+pad)
        variants.append({'label':f'loop={kind};pad={placement}:{amount};unsigned-index-load',
                         'source':source,'parent_source_sha256':metadata['source_sha256'],
                         'derived_from_round1_label':'units=bytes;walk=bytes;stack=both;reuse=0',
                         'derived_from_source_sha256':parent_hash})
assert len(variants)==len({row['source'] for row in variants})==32
(OUT/'variants-round2.json').write_text(json.dumps(variants,indent=2))
print(json.dumps({'variants':len(variants),'measured_parent_sha256':parent_hash}))
