"""Assisted binary/header-led 64-bit timer reconstruction; not a generic solver."""
import hashlib
import importlib
import json
from pathlib import Path

from eval import agentrepair


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    path=root/'eval/results/failure-coverage-fixes-replay-v3-artifacts/__osTimerInterrupt.json'
    previous=json.loads(path.read_text())
    source=Path(previous['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=previous['result']['best_source_sha256']:
        raise ValueError('source identity changed')
    output=root/'eval/results/timer-wide-reconstruction-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite probe')
    first='''        temp_t6 = (u32)sp24->interval;
        if ((temp_t6 >= 0U) && ((temp_t6 > 0U) || (sp1C < (u32) sp24->value))) {
            sp18 = &sp24->value;
            temp_t3 = (u32)sp24->value;
            sp24->value = (OSTime)(temp_t3 - sp1C);
            sp24->unk10 = (u32) ((sp24->unk10 - 0) - (temp_t3 < sp1C));
            __osSetTimerIntr(/* u64+0x0 */ sp24->unk10, /* u64+0x4 */ sp24->unk14);'''
    second='''        temp_t8 = sp24->unk8;
        temp_t9_2 = sp24->unkC;
        if ((temp_t8 != 0) || (temp_t9_2 != 0)) {
            sp24->unk10 = temp_t8;
            sp24->unk14 = temp_t9_2;'''
    if source.count(first)!=1 or source.count(second)!=1:
        raise ValueError('source-bound wide-operation spans changed')
    candidate=source.replace(first,'''        if (sp24->value > sp1C) {
            sp24->value -= sp1C;
            __osSetTimerIntr(sp24->value);''').replace(second,'''        if (sp24->interval != 0) {
            sp24->value = sp24->interval;''')
    agentrepair._refuse_frozen_heldout(root/'eval/sets','__osTimerInterrupt')
    manifest=output.with_name(output.stem+'-inputs.json')
    if manifest.exists():
        raise ValueError('refusing to overwrite inputs')
    agentrepair._atomic_json(manifest,{'kind':'assisted-word-pair-operation-reconstruction',
        'parent_receipt':str(path),'parent_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'parent_source_sha256':previous['result']['best_source_sha256'],
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),
        'hypotheses':['offset10/14 is value high/low, not interval low/value low',
            'unsigned pair comparison and subtract-with-borrow denote u64 compare/subtract',
            'offset8/C nonzero test and copy10/14 denote interval !=0 and value=interval',
            '__osSetTimerIntr(OSTime) takes one C wide argument, not two word arguments'],
        'reference_bodies_used':False,'integration_requested':False,'model_calls':0,
        'scope':'target-specific assisted causal probe; not automatic generalization'})
    no_model=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result=agentrepair.run(repo=repo,db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',
        function='__osTimerInterrupt',source=candidate,source_parent_attempt_id=None,
        out=output,best_source_out=output.with_suffix('.best.c'),model='zero-model-assisted',
        endpoint='http://127.0.0.1:1',draws=1,depth=1,beam=3,max_calls=0,timeout=1,
        think='low',num_thread=1,temperature=0,num_predict=1,seed=20260906,cache_dir=None,
        verbose=False,provider=no_model,resilient=True,semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({'exact':result['exact'],'attempt':result['best_attempt_id'],
        'residual':result['best_residual'],'semantic_status':(result.get('semantic_validation') or {}).get('status')}))


if __name__=='__main__':
    main()
