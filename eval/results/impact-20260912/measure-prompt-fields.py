import json
import sys
from pathlib import Path
row=json.loads(Path(__file__).with_name('prompt-'+(sys.argv[1] if len(sys.argv)>1 else '3882')+'.json').read_text())
p=row['prompt_context']
keys=['PUBLIC ABI (read-only):','FAILING OBSERVABLES AND EXECUTED VALUE TREES:', 'CURRENT C:',
'ACTUAL INCLUDED TYPE DEFINITIONS (header-assisted, read-only):','TARGET INSTRUCTIONS (read-only; byte polish is secondary while behavior fails):',
'RECENT VERIFIED REJECTIONS/OUTCOMES:','DETERMINISTIC COMPILE OBLIGATIONS:','READ-ONLY STACK RESULT AND CALL ABI EVIDENCE:']
items=sorted((p.index(k),k) for k in keys if k in p)
print('SECTIONS',[(k,(items[i+1][0] if i+1<len(items) else len(p))-n) for i,(n,k) in enumerate(items)])
packet=json.loads(p.split('FAILING OBSERVABLES AND EXECUTED VALUE TREES:\n',1)[1].split('\nCURRENT C:\n',1)[0])
print('CONTRACTS', {k:{a:len(json.dumps(b)) for a,b in v.items() if len(json.dumps(b))>250} for k,v in packet['call_contracts'].items()})
print('COMPACT',len(json.dumps(packet,separators=(',',':'))),len(json.dumps(packet)))
for side, value in packet['primary_counterexample'].get('concrete_callee_executions',{}).items():
    for call in value.get('calls',[]):
        for key in ('instruction_prefix','instruction_suffix'):
            call.pop(key,None)
print('NOTRACES',len(json.dumps(packet,separators=(',',':'))))
