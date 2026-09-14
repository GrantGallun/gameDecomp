"""Join original fresh-v2 failures to exposed-source repair evidence, not new solves."""
import hashlib
import importlib
import json
from pathlib import Path
from eval import agentrepair

audit = importlib.import_module('eval.experiments.campaign-gap-audit.summarize')


def read(path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def bind_probe(checkpoint, function, path):
    state, checkpoint_hash = read(checkpoint)
    parent = state['nodes'][function]
    receipt, receipt_hash = read(path)
    inputs, input_hash = read(path.with_name(path.stem+'-inputs.json'))
    if inputs['function'] != function or inputs.get('integration_requested') is not False:
        raise ValueError('probe identity or integration scope mismatch')
    if inputs.get('reference_bodies_used') is not False or inputs.get('model_calls') != 0:
        raise ValueError('probe regime mismatch')
    result = receipt['result']
    intake = receipt.get('kind') == 'source-bound-intake-replay'
    if intake:
        if inputs['inputs'].get('base.c') != parent['source_sha256'] or receipt.get('inputs_unchanged') is not True:
            raise ValueError('fresh intake base or input-immutability receipt mismatch')
        source_path, source_hash = result['source'], result['source_sha256']
        residual, semantic = result['residual'], {}
        attempt_id = result['attempt_id']
    else:
        if inputs.get('checkpoint_sha256') != checkpoint_hash or inputs['source_sha256'] != parent['source_sha256']:
            raise ValueError('original checkpoint/source binding mismatch')
        if receipt['root']['source_sha256'] != parent['source_sha256'] or result['calls_attempted'] != 0:
            raise ValueError('replayed root or actual call budget mismatch')
        source_path, source_hash = result['best_source_path'], result['best_source_sha256']
        residual, semantic = result['best_residual'], result.get('semantic_validation') or {}
        attempt_id = result['best_attempt_id']
    source = audit.local_path(source_path).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest() != source_hash:
        raise ValueError('probe output source mismatch')
    if semantic and semantic.get('source_sha256') != source_hash:
        raise ValueError('semantic evidence belongs to another candidate')
    return {'receipt':str(path),'receipt_sha256':receipt_hash,'input_receipt_sha256':input_hash,
        'checkpoint_sha256':checkpoint_hash, 'original_source_sha256':parent['source_sha256'],
        'attempt_id':attempt_id,'source_sha256':source_hash,'compiled':residual.get('compiled'),
        'frontend_passed':(residual.get('frontend') or {}).get('passed'),
        'positional_byte_distance':residual.get('positional_byte_distance'),
        'semantic_status':semantic.get('status','not_tested'), 'semantic_counts':semantic.get('counts'),
        'environment_obligations':semantic.get('environment_obligations'),
        'model_calls':0,'exposure':'previously exposed source, not an unseen transfer result',
        'target_build_validation':'historical receipt only; current WSL inputs not revalidated by this audit',
        'causal_accounting_complete':False}


def main():
    root = Path.cwd()/'eval/results'
    output = root/'failure-coverage-fresh-v2-progress-audit-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite progress audit')
    probes = {'osCreateThread':'osCreateThread-runtime-callback-v1.json',
        '__osSiRawStartDma':'si-hardware-environment-v1.json', '_Ldtob':'ldtob-ranked-intake-v1.json'}
    rows = []
    for batch in (1,2):
        checkpoint = root/f'failure-coverage-fresh-paired-v2-batch-{batch}.json'
        original = audit.summarize(checkpoint)
        for row in original['rows']:
            row['postfix_probe'] = (bind_probe(checkpoint,row['function'],root/probes[row['function']])
                                   if row['function'] in probes else None)
            rows.append(row)
    agentrepair._atomic_json(output,{'kind':'fresh-cohort-progress-accounting','rows':rows,
        'scope':'original stages preserved; later exposed-source probes separately bound',
        'causal_accounting_complete':False,'current_target_build_inputs_revalidated':False,
        'remaining_work':'causal diagnosis and replay of remaining failures; frozen/fresh validation of latest machinery'})
    print({'rows':len(rows),'source_bound_postfix_probes':sum(r['postfix_probe'] is not None for r in rows)})


if __name__ == '__main__':
    main()
