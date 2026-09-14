"""Inventory historical selection receipts; unknown schemas remain audit debt."""
import argparse
from collections import Counter
import hashlib
import importlib
import json
from pathlib import Path

KINDS = {'resumable-completion-campaign','preselected-development-expansion',
         'preselected-paired-stratified-development','differential-debugger-wavefront',
         'frozen-wavefront-experiment','logic-first-connected-dev-cluster','legacy-function-experiment-jsonl',
         'dag-ordered-differential-pipeline-census'}


def inventory(directory):
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    selected,other,unreadable=[],[],[]
    for path in sorted(directory.glob('*.json')):
        raw=path.read_bytes()
        try:
            row=module.read_selection_receipt(raw)
        except (ValueError,UnicodeError) as exc:
            unreadable.append({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'error':str(exc)})
            continue
        kind=row.get('kind') if isinstance(row,dict) else None
        if kind in KINDS:
            selected.append(path)
        else:
            other.append({'path':str(path),'kind':kind,'sha256':hashlib.sha256(raw).hexdigest()})
    return selected,other,unreadable


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--results',type=Path,required=True)
    parser.add_argument('--historical-db',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('refusing to overwrite historical audit')
    module=importlib.import_module('eval.experiments.campaign-gap-audit.run_expansion')
    selected,other,unreadable=inventory(args.results)
    valid,rejected=[],[]
    for path in selected:
        try:
            module.historical_exposure([],[path])
        except ValueError as exc:
            rejected.append({'path':str(path),'error':str(exc),
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        else:
            valid.append(path)
    names,addresses,proof=module.historical_exposure(args.historical_db,valid)
    receipt={'kind':'historical-selection-exposure-inventory','selected_receipts':len(selected),
        'exposed_names':sorted(names),'exposed_addresses':sorted(addresses),'bindings':proof,
        'other_receipts':other,'unreadable_receipts':unreadable,
        'rejected_selection_receipts':rejected,
        'complete_historical_exposure_proven':False,
        'scope':'top-level known selection schemas plus DB attempts; unrecognized schemas and external inspection remain debt'}
    from eval import agentrepair
    agentrepair._atomic_json(args.output,receipt)
    print(json.dumps({'selection_receipts':len(selected),'names':len(names),
        'other_kinds':dict(Counter(str(row['kind']) for row in other)),
        'unreadable':unreadable,'rejected':rejected}))


if __name__=='__main__':
    main()
