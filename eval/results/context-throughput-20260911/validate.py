import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval.campaign_workers import isolate
from solver import workspace

directory=Path(__file__).parent
reports={name:json.loads((directory/(name+'.json')).read_bytes()) for name in ('adaptive','fixed')}
validation=[]
for mode,report in reports.items():
    for index,row in enumerate(report['results']):
        result={'mode':mode,'index':index,'proposal_id':row['proposal_id'],'applied':row['applied']}
        if row['applied']:
            function=row['function']
            repo=isolate(Path('/home/grant/decomp/sbk1'),Path('/home/grant/decomp/context-validation-20260911')/f'{mode}-{index}',function)
            source=(directory/f'{mode}-{index}.c').read_text()
            attempt=workspace.score(workspace.bootstrap(repo,function),repo,function,source)
            result.update(compiled=attempt.compiled,score=attempt.score,exact=attempt.exact,
                          frontend_passed=(attempt.frontend or {}).get('passed'))
        validation.append(result)
for left,right in zip(reports['adaptive']['results'],reports['fixed']['results']):
    assert left['proposal_id']==right['proposal_id']
    assert left['metadata']['_prompt_sha256']==right['metadata']['_prompt_sha256']
    for key in ('num_predict','temperature','seed','num_thread'):
        assert left['metadata']['_request_options'][key]==right['metadata']['_request_options'][key]
summary={mode:{'seconds':r['seconds'],
    'request_seconds':sum(x['wall_seconds'] for x in r['results']),
    'load_seconds':sum(x['metadata'].get('load_duration',0)/1e9 for x in r['results']),
    'generated_tokens':sum(x['metadata'].get('eval_count',0) for x in r['results']),
    'applied':sum(x['applied'] for x in r['results']),
    'compiled':sum(bool(x.get('compiled')) for x in validation if x['mode']==mode),
    'contexts':[x['metadata']['_request_options']['num_ctx'] for x in r['results']]}
    for mode,r in reports.items()}
summary['wall_speed_ratio']=summary['adaptive']['request_seconds']/summary['fixed']['request_seconds']
summary['identical_candidate_pairs']=sum(a.get('candidate_sha256')==b.get('candidate_sha256') and a['applied'] and b['applied'] for a,b in zip(reports['adaptive']['results'],reports['fixed']['results']))
summary['validation']=validation
(directory/'comparison.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
