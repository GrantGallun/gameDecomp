"""Closed word-pair return reconstruction, gated by admitted binary callees.

Candidate hypotheses only. Every use of each temporary must fit the closed
transliteration grammar; changing a prototype never silently affects other bodies.
"""
import hashlib
import re

from solver import callee_execution, project_headers, repair_context


def propose(source, function, environment, *, byteorder):
    report={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'changes':[],'declines':[],'source':source}
    if byteorder!='big':
        report['declines'].append('word view requires explicit big-endian target')
        return report
    masked=project_headers._mask_noncode(source)
    definition,end=repair_context.definition(source,function)
    start=definition.end()
    for contract in callee_execution.source_contracts(source,environment):
        if contract['status']!='result-width-conflict':
            continue
        name=contract['callee']
        prototype=re.compile(r'(?m)^[ \t]*(?:extern\s+)?s32\s+'+re.escape(name)+r'\s*\(\s*s32\s*,\s*s32\s*,\s*s32\s*,\s*s32\s*\)\s*;')
        declarations=list(prototype.finditer(masked))
        edits=[]
        reason=None
        if len(declarations)!=1 or declarations[0].end()>start:
            reason='requires unique file-scope four-word s32 prototype'
        else:
            decl=declarations[0]
            outside=masked[:start]+(' '*(end-start))+masked[end:]
            outside=outside[:decl.start()]+(' '*(decl.end()-decl.start()))+outside[decl.end():]
            if re.search(r'\b'+re.escape(name)+r'\b',outside):
                reason='callee has uses outside selected function'
        calls=list(re.finditer(r'\b(\w+)\s*=\s*'+re.escape(name)+r'\s*\(',masked[start:end]))
        if not calls:
            reason=reason or 'no plain assigned result'
        if len(calls)!=len(re.findall(r'\b'+re.escape(name)+r'\b',masked[start:end])):
            reason=reason or 'unclassified callee use'
        locals_seen=set()
        for call in calls:
            local=call[1]
            prefix=masked[start:start+call.start()].rstrip()
            if prefix and prefix[-1] not in ';{}':
                reason=reason or 'result assignment is not a standalone statement'
                break
            if local in locals_seen:
                reason=reason or 'result temporary assigned more than once'
                break
            locals_seen.add(local)
            token=r'\b'+re.escape(local)+r'\b'
            body=masked[start:end]
            local_decls=list(re.finditer(r'(?m)^[ \t]*s32\s+'+token+r'\s*;',body))
            # Nested braces may shadow names; reject repeated declarations/uses
            # outside our single plain declaration through exhaustive use accounting.
            if len(local_decls)!=1:
                reason=reason or 'result must be a plain local s32'
                break
            local_decl=local_decls[0]
            used=set()
            occurrences=list(re.finditer(token,body))
            def cover(a,b,replacement):
                edits.append((start+a,start+b,replacement))
                used.update(m.start() for m in occurrences if a<=m.start()<b)
            cover(local_decl.start(),local_decl.end(),
                'union { u64 wide; struct { s32 high; u32 low; } words; } '+local+';')
            cover(call.start(1),call.end(1),local+'.wide')
            for low in re.finditer(r'\(u32\)\s*\(u64\)\s*'+token,body):
                cover(low.start(),low.end(),local+'.words.low')
            for occurrence in occurrences:
                if occurrence.start() in used:
                    continue
                before=body[:occurrence.start()]
                after=body[occurrence.end():]
                # Original scalar result is v0 (high word); only the motivating
                # direct high-word copy and subtract expression are admitted.
                if (re.search(r'\b\w+\s*=\s*$',before) and re.match(r'\s*;',after)) or (
                    re.search(r'-\s*$',before) and re.match(r'\s*\)',after)):
                    cover(occurrence.start(),occurrence.end(),local+'.words.high')
                else:
                    reason=reason or 'unclassified result use: '+local
            if not any(local+'.words.low'==edit[2] for edit in edits):
                reason=reason or 'no explicit lost-low-word extraction'
        if reason:
            report['declines'].append({'callee':name,'reason':reason})
            continue
        # One coordinated candidate per invocation prevents offset drift across
        # multiple independent callee rewrites; later rounds can handle the rest.
        decl=declarations[0]
        replacement=re.sub(r'\bs32\b','u64',source[decl.start():decl.end()],count=1)
        edits.append((decl.start(),decl.end(),replacement))
        ordered=sorted(edits)
        if any(a[1]>b[0] for a,b in zip(ordered,ordered[1:])):
            report['declines'].append({'callee':name,'reason':'overlapping source edits'})
            continue
        candidate=source
        for a,b,replacement in reversed(ordered):
            candidate=candidate[:a]+replacement+candidate[b:]
        report.update(source=candidate,candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
            changes=[{'start':a,'end':b,'before':source[a:b],'after':c} for a,b,c in ordered],
            contract=contract,scope='binary-bound candidate hypothesis; compilation and differential validation required')
        return report
    return report
