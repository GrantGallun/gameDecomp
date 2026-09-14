import json
from pathlib import Path
from solver import prompt_budget, modelrepair

directory=Path(__file__).parent
results=[]
for path in sorted(directory.glob('prompt-*.json')):
    if not path.stem.removeprefix('prompt-').isdigit():
        continue
    row=json.loads(path.read_text())
    before=row['prompt_context']
    marker='FAILING OBSERVABLES AND EXECUTED VALUE TREES:\n'
    if marker not in before:
        continue
    prefix, tail=before.split(marker,1)
    raw,suffix=tail.split('\nCURRENT C:\n',1)
    packet=json.loads(raw)
    compact=prompt_budget.semantic_packet(packet)
    after=prefix+marker+json.dumps(compact,separators=(',',':'))+'\nCURRENT C:\n'+suffix
    # semantic_prompt decides before search appends its obligations/strategy.
    base_end=after.find('\nDETERMINISTIC COMPILE OBLIGATIONS:')
    base=after if base_end<0 else after[:base_end]
    function_start=base.find('SEMANTIC REPAIR OBJECTIVE:')
    if function_start>=0:
        base=base[function_start:]
    if len(base.encode())>48000 and packet.get('other_failure_classes'):
        compact=prompt_budget.semantic_packet(packet,omit_secondary=True)
        after=prefix+marker+json.dumps(compact,separators=(',',':'))+'\nCURRENT C:\n'+suffix
    def budget(prompt):
        try:
            return {'fits':True,**prompt_budget.context_budget(prompt,6000,num_ctx=32768,response_schema=modelrepair.EDIT_SCHEMA)}
        except prompt_budget.ContextBudgetError as e:
            return {'fits':False,**e.context_budget}
    stats={'id':row['id'],'before':budget(before),'after':budget(after),
           'before_chars':len(before),'after_chars':len(after)}
    results.append(stats)
    if not stats['before']['fits'] and stats['after']['fits']:
        (directory/('compact-prompt-'+str(row['id'])+'.txt')).write_text(after)
    print(json.dumps({k:v for k,v in stats.items() if k not in ('before','after')}|{
        'tokens_before':stats['before']['required_tokens'],'tokens_after':stats['after']['required_tokens'],
        'reopened':not stats['before']['fits'] and stats['after']['fits']}))
(directory/'prompt-compaction-replay.json').write_text(json.dumps(results,indent=2))
