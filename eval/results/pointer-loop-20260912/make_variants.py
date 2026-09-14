"""Source-bound experiments for the selected real traversal, not a general rule."""
import hashlib
import itertools
import json
from pathlib import Path
import re

OUT=Path(__file__).resolve().parent
SELECT=OUT/'selection/traversal-pool/drawCharacterSelectCoursePreviewFrame'
source=(SELECT/'source.c').read_text()
metadata=json.loads((SELECT/'metadata.json').read_bytes())
assert hashlib.sha256(source.encode()).hexdigest()==metadata['source_sha256']

def change(text,old,new):
    assert text.count(old)==1,old
    return text.replace(old,new,1)

def variant(units='original',walk='bytes',stack='none',reuse=False):
    result=source
    if units=='typed':
        result=change(result,'(sp4E * 0x2A) + gCharacterSelectCoursePreviewFrameTileMaps',
                      'gCharacterSelectCoursePreviewFrameTileMaps + (sp4E * 21)')
        result=change(result,'*(&gCharacterSelectCoursePreviewFrameCornerTileMaps + (sp4E * 0x2A))',
                      '*(&gCharacterSelectCoursePreviewFrameCornerTileMaps + (sp4E * 21))')
    elif units=='bytes':
        result=change(result,'(sp4E * 0x2A) + gCharacterSelectCoursePreviewFrameTileMaps',
                      '(u16 *)((unsigned char *)gCharacterSelectCoursePreviewFrameTileMaps + sp4E * 0x2A)')
        result=change(result,'*(&gCharacterSelectCoursePreviewFrameCornerTileMaps + (sp4E * 0x2A))',
                      '*(u16 *)((unsigned char *)&gCharacterSelectCoursePreviewFrameCornerTileMaps + sp4E * 0x2A)')
    if walk=='typed':
        for name in ('var_s0','var_s0_2'):
            result=change(result,f'{name} = (u16 *)((unsigned char *){name} + 2);',f'{name}++;')
        result=change(result,'(*(u16 *)((u8 *)(var_s0_2) + 0x20))','var_s0_2[16]')
        result=change(result,'(*(u16 *)((u8 *)(var_s0_2) + 0x24))','var_s0_2[18]')
    elif walk=='indexed-first':
        result=change(result,'*var_s0, 0U, 0x100U','sp44[var_s1], 0U, 0x100U')
        result=change(result,'var_s0 = (u16 *)((unsigned char *)var_s0 + 2);','')
    if stack in {'selector','both'}:
        result=change(result,'    u16 sp4E;','    volatile u16 sp4E;')
    if stack in {'base','both'}:
        result=change(result,'    u16 *sp44;','    u16 *volatile sp44;')
    if reuse:
        result=change(result,'    u16 *var_s0_2;\n','')
        result=re.sub(r'\bvar_s0_2\b','var_s0',result)
    return result

rows=[]
seen={source}
def add(units='original',walk='bytes',stack='none',reuse=False):
    text=variant(units,walk,stack,reuse)
    if text in seen: return
    seen.add(text)
    rows.append({'label':f'units={units};walk={walk};stack={stack};reuse={int(reuse)}',
                 'source':text,'parent_source_sha256':metadata['source_sha256'],
                 'hypothesis_kind':'byte-stride-repair' if units!='original' else 'source-shape-control'})

# Keep unchanged-unit controls and independent factors; combinations are tested
# even if a component scores worse, rather than requiring monotonic hill climbing.
add(walk='typed')
add(walk='indexed-first')
add(stack='selector')
add(stack='base')
add(stack='both')
add(reuse=True)
for units,stack,reuse in itertools.product(('typed','bytes'),('none','selector','base','both'),(False,True)):
    add(units=units,stack=stack,reuse=reuse)
for stack,reuse in itertools.product(('none','selector','base','both'),(False,True)):
    add(units='typed',walk='typed',stack=stack,reuse=reuse)
add(units='typed',walk='indexed-first')
add(units='typed',walk='indexed-first',stack='both')
assert len(rows)==32
(OUT/'variants-round1.json').write_text(json.dumps(rows,indent=2))
(OUT/'experiment.json').write_text(json.dumps({
    'function':metadata['name'],'source_binding':metadata,
    'variants':len(rows),'model_calls':0,
    'target_facts':['selector multiply chain computes 42 bytes at 0x8001D350..0x8001D368',
                    'pointer induction is +2 bytes at 0x8001D3EC and 0x8001D48C',
                    'corner selector multiply is 42 bytes at 0x8001D4A4..0x8001D4C0'],
    'scope':'Unit correction is a behavior repair hypothesis; other dimensions test source shape. No blanket equivalence claim.'},indent=2))
print(json.dumps({'variants':len(rows),'source_sha256':metadata['source_sha256']}))
