from pathlib import Path
from solver import rewrites as r, principle_variants as p
import re
root=Path('eval/results/residual-patterns-20260912')
source=(root/'startEndingSlashRepeatAnim.selected.c').read_text()
diff=(root/'startEndingSlashRepeatAnim.selected.diff').read_text()
t,c=r.diffrepair._streams(diff)
changes=[(a,b) for a,b in zip(t,c) if a!=b]
print('changes',changes,'safe',r.diffrepair.aligned_pairs(diff))
masked=r.c89._mask(source)
defs=[m for m in re.finditer(r'\b([A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{',masked) if m[1] not in {'if','for','while','switch'}]
print('defs',[m[0] for m in defs])
span=p._body_span(source,defs[0][1]);body=source[span[0]:span[1]]
pre,decl,stop,_=p._leading_declarations(body)
print('pre',repr(pre),'decl',decl,'tail',repr(body[stop:][:70]))
