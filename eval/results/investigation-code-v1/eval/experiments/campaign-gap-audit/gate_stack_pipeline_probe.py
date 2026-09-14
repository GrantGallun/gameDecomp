"""Replay the saved gate input through automatic normalization, with zero model calls."""
import importlib
import json
import hashlib
from pathlib import Path
from eval import agentrepair


def main():
    root = Path('/mnt/c/Code/gameDecomp')
    parent = root/'eval/results/gate-partial-stores-v1.json'
    previous = json.loads(parent.read_text())['result']
    source = Path(previous['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest() != previous['best_source_sha256']:
        raise ValueError('parent source changed')
    output = root/'eval/results/gate-stack-object-pipeline-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite probe')
    provider = importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result = agentrepair.run(repo=Path('/home/grant/decomp/sbk1'),
        db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',function='renderCourseGateObject',
        source=source,source_parent_attempt_id=previous['best_attempt_id'],out=output,
        best_source_out=output.with_suffix('.best.c'),model='zero-model-pipeline',endpoint='http://127.0.0.1:1',
        draws=1,depth=1,beam=3,max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
        seed=20260906,cache_dir=None,verbose=False,provider=provider,resilient=True,
        semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({**{k:result.get(k) for k in ('best_attempt_id','best_source_sha256','exact')},
        'score': result['best_residual']['weighted_progress_score'],
        'counts': (result.get('semantic_validation') or {}).get('counts'),
        'semantic_status': (result.get('semantic_validation') or {}).get('status')}))


if __name__ == '__main__':
    main()
