"""Summarize the completed fixed-parent three-protocol experiment."""
import json
from pathlib import Path

folder=Path(__file__).resolve().parent/'patch-protocol-pilot-20260910-v1'
report=json.loads((folder/'report.json').read_text())
assert report['status']=='complete'
arms=('slots_only','two_stage','focused_retry')
lines=['# Three patch protocols: fixed-parent DEV comparison', '',
       'Three saved failures, rotated arm order, two calls per arm/function,',
       '4096 output-token cap per call. Two-stage spends a call selecting locations;',
       'the other arms can produce two code proposals. Focused retry uses reduced context.',
       'All candidates are separate trials against the same unchanged original parent.', '',
       '| Approach | Functions with applicable patch | With frontend-passing compile | With score gain and frontend pass | Exact | Calls |',
       '|---|---:|---:|---:|---:|---:|']
for arm in arms:
    rows=[x for c in report['cases'] for x in c['arms'][arm]]
    applicable=sum(any(x['status']=='application_valid' for x in c['arms'][arm]) for c in report['cases'])
    frontend=sum(any(x.get('compiled') and x.get('frontend_pass') for x in c['arms'][arm]) for c in report['cases'])
    improved=sum(any(x.get('compiled') and x.get('frontend_pass') and x.get('score',0)>c['parent']['score']
                     for x in c['arms'][arm]) for c in report['cases'])
    exact=sum(any(x.get('exact') for x in c['arms'][arm]) for c in report['cases'])
    lines.append(f'| {arm} | {applicable}/3 | {frontend}/3 | {improved}/3 | {exact}/3 | {len(rows)} |')
lines+=['', '| Function | Approach | Outcomes in call order |', '|---|---|---|']
for case in report['cases']:
    for arm in arms:
        outcomes=[]
        for row in case['arms'][arm]:
            text=row['status']
            if text=='application_valid':
                text+='; '+('frontend pass' if row.get('frontend_pass') and row.get('compiled') else 'compile/frontend failed')
                text+=f"; score {row.get('score')}"
            elif row.get('error'):
                text+='; '+row['error'].replace('|','/')
            outcomes.append(text)
        lines.append(f"| {case['function']} | {arm} | {' / '.join(outcomes)} |")
lines+=['', 'This is a small, deliberately failure-enriched development comparison, not a',
        'general model success rate. Selection success is not counted as an applicable',
        'patch. Application-valid means the existing parser/application gates accepted',
        'the edit; it is not proof of public ABI or behavioral correctness. Compiler,',
        'frontend and exact-object results are recorded separately. No semantic gain',
        'or source integration is claimed. The live campaign is unchanged.', '',
        '37 protocol and existing model-repair tests passed. Full prompts, responses,',
        'private attempt databases, original sources, candidate C and compiler objects',
        'are retained beside report.json. Failed trials remain in the report.', '']
(folder/'README.md').write_text('\n'.join(lines))
print('\n'.join(lines[:12]))
