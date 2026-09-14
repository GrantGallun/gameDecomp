"""Read campaign receipts and emit disjoint stopping stages, not match guesses."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from eval import agentrepair


def local_path(value):
    if Path.cwd().drive and value.startswith('/mnt/c/'):
        return Path('C:/' + value[len('/mnt/c/'):])
    return Path(value)


def summarize(path):
    raw = path.read_bytes()
    state = json.loads(raw)
    if state.get('inflight'):
        raise ValueError('campaign still has an unfinished work item: ' + str(path))
    rows, actual_calls = [], 0
    for name, node in sorted(state['nodes'].items()):
        residual = node.get('residual') or {}
        frontend = residual.get('frontend') or {}
        semantic = node.get('semantic_validation') or {}
        if semantic and semantic.get('source_sha256') != node.get('source_sha256'):
            raise ValueError('semantic source identity mismatch: ' + name)
        if node['status'] == 'parked': stage = 'parked'
        elif residual.get('compiled') is not True: stage = 'not_compiling'
        elif frontend.get('passed') is not True: stage = 'frontend_rejected_or_unavailable'
        elif node['status'] in {'object_exact','integrated'}: stage = 'accepted_object_exact'
        elif semantic.get('status') == 'observed_pass': stage = 'sampled_pass_nonexact'
        elif (semantic.get('counts') or {}).get('failed', 0): stage = 'differential_disagreement'
        else: stage = 'semantic_untested_or_unavailable'
        for job in node['jobs']:
            receipt = json.loads(local_path(job['receipt']).read_text())
            actual_calls += (receipt.get('result') or receipt).get('calls_attempted', 0) or 0
        rows.append({'function':name, 'stage':stage, 'attempt_id':node.get('attempt_id'),
            'source_sha256':node.get('source_sha256'), 'score':node.get('score'),
            'positional_byte_distance':residual.get('positional_byte_distance'),
            'target_text_bytes':residual.get('target_text_bytes'),
            'semantic_counts':semantic.get('counts'), 'target_coverage':semantic.get('target_coverage'),
            'semantic_debt':semantic.get('debt'), 'blocker':node.get('blocker'),
            'first_frontend_errors':[line for line in frontend.get('diagnostics','').splitlines()
                                     if 'error:' in line][:4],
            'profiles':[job['profile'] for job in node['jobs']]})
    return {'checkpoint':str(path), 'checkpoint_sha256':hashlib.sha256(raw).hexdigest(),
        'status':state['status'], 'cohort_size':len(rows), 'actual_model_calls':actual_calls,
        'stages':dict(Counter(row['stage'] for row in rows)), 'rows':rows,
        'regime':state['regime'], 'forked_from':state.get('forked_from'),
        'complete_c_decompilation':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkpoints',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('refusing to overwrite audit')
    receipt={'kind':'campaign-stopping-stage-audit','campaigns':[summarize(p) for p in args.checkpoints]}
    agentrepair._atomic_json(args.output,receipt)
    for row in receipt['campaigns']:
        print(json.dumps({k:row[k] for k in ('checkpoint','status','cohort_size','actual_model_calls','stages')}))


if __name__ == '__main__': main()
