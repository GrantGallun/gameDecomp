"""Header-locked definition hypotheses retaining candidate-local body views."""
import hashlib
import re
from pathlib import Path
from solver import global_scalar_view, project_headers, repair_context, type_transaction


WIDTHS={'s8':8,'u8':8,'char':8,'signed char':8,'unsigned char':8,
        's16':16,'u16':16,'short':16,'unsigned short':16,
        's32':32,'u32':32,'int':32,'unsigned int':32,'signed int':32,
        'long':32,'unsigned long':32,'unsigned':32,'signed':32,'void':0}


def width(tokens):
    spelling=' '.join(tokens)
    if re.fullmatch(r'(?:(?:struct|union) )?\w+ \*',spelling):return 32
    return WIDTHS.get(spelling)


def propose(repo, source, function, diagnostics, *, big_endian_o32=False):
    report={'source':source,'changes':[],'declines':[],
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'scope':'header-assisted public signature with candidate-local body aliases; not original type recovery'}
    if not big_endian_o32 or f"conflicting types for '{function}'" not in diagnostics:return report
    definition,end=repair_context.definition(source,function)
    mask=project_headers._mask_noncode(source);body=mask[definition.end():end-1]
    if re.search(r'(?m)^\s*#',body):return report
    line_number=source.count('\n',0,definition.start())+1
    line=source.splitlines()[line_number-1]
    diagnostic=re.search(r'^candidate\.c:'+str(line_number)+r":\d+: error: conflicting types for '"+
                         re.escape(function)+r"'\n\s*"+str(line_number)+r' \| (.*)',diagnostics,re.M)
    if not diagnostic or diagnostic[1]!=line or '\t' in line:return report
    abi=type_transaction.contract(Path(repo),source,function)
    old=type_transaction.signature(source[definition.start():definition.end()],function)
    if abi['status']!='locked' or old is None or old==abi['shape']:return report
    new=abi['shape'];old_return,old_params=old;new_return,new_params=new
    if (len(old_params)!=len(new_params) or len(old_params)>8
            or width(old_return)!=width(new_return) or width(old_return) is None
            or any(width(a) is None or width(a)!=width(b) or width(a)==0 for a,b in zip(old_params,new_params))):
        report['declines'].append('requires corresponding supported o32 parameter/return widths')
        return report
    raw=[] if not old_params else definition[2].split(',')
    if len(raw)!=len(old_params):return report
    names=[]
    for parameter in raw:
        named=re.fullmatch(r'\s*[\w\s*]+?\b([A-Za-z_]\w*)\s*',parameter)
        if not named:return report
        names.append(named[1])
    if len(set(names))!=len(names):return report
    identifiers=set(re.findall(r'\b[A-Za-z_]\w*\b',source+'\n'+global_scalar_view.header_text(repo,source)))
    if re.search(r'(?m)^\s*#\s*define\s+'+re.escape(function)+r'\b',source+'\n'+global_scalar_view.header_text(repo,source)):return report
    params=[];aliases=[]
    for i,(name,a,b) in enumerate(zip(names,old_params,new_params)):
        if a==b:params.append(' '.join(b)+' '+name);continue
        fresh='gd_abi_'+name;suffix=0
        while fresh in identifiers:
            suffix+=1;fresh='gd_abi_'+name+'_'+str(suffix)
        identifiers.add(fresh)
        params.append(' '.join(b)+' '+fresh)
        aliases.append('    '+' '.join(a)+' '+name+' = ('+' '.join(a)+')'+fresh+';')
    edits=[]
    if old_return!=new_return:
        # Ordinary returns only, retaining one evaluation and the expression's
        # original pointer/integer operations until the ABI boundary conversion.
        for match in re.finditer(r'\breturn\b([^;{}]*);',body):
            expression=source[definition.end()+match.start(1):definition.end()+match.end(1)].strip()
            if not expression:return report
            edits.append((definition.end()+match.start(),definition.end()+match.end(),
                          'return ('+' '.join(new_return)+')('+expression+');'))
        if len(edits)!=len(re.findall(r'\breturn\b',body)):return report
    replacement=' '.join(new_return)+' '+function+'('+(', '.join(params) or 'void')+') {'
    if aliases:replacement+='\n'+'\n'.join(aliases)
    edits.append((definition.start(),definition.end(),replacement))
    for a,b,text in sorted(edits,reverse=True):report['source']=report['source'][:a]+text+report['source'][b:]
    # The ordinary ABI lock independently checks the emitted public interface.
    type_transaction.validate(source,report['source'],function,abi)
    report['changes']=[dict(before=source[a:b],after=text,start=a,end=b) for a,b,text in sorted(edits)]
    report['header_declarations']=abi['declarations']
    report['aliases']=aliases
    return report
