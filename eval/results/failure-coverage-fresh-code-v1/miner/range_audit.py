"""Read-only comparison of historical evidence with ROM-bound extraction.

Never calls the mutating extractor, overwrites history or supplies reference C.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

from miner import evidence

FIELDS = ('kind','addr','func_addr','op','base','base_reg','offset','width','signed',
          'class','access','is_load','target_addr')


def audit(repo,db):
    files = list((repo/'build').glob('*.elf'))
    if len(files)!=1:
        raise ValueError('requires one unambiguous built ELF')
    elf = files[0]
    funcs = evidence.disassemble(elf)
    bindings = evidence.bind_repo_rom(repo,funcs)
    expected = {}
    for func in funcs:
        for row in evidence.evidence_rows(func,evidence.resolve_bases(func)):
            key = (row['addr'],row['kind'])
            if key in expected:
                raise ValueError('overlapping function evidence ownership: '+repr(key))
            expected[key] = {field:row.get(field) for field in FIELDS}
    with sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True) as conn:
        conn.row_factory = sqlite3.Row
        prior = {(row['addr'],row['kind']):dict(row) for row in conn.execute('SELECT '+','.join(FIELDS)+' FROM evidence')}
        inventory = [dict(row) for row in conn.execute('SELECT addr,name,size,insn_count,is_leaf FROM functions')]
    by_identity = {(f.addr,f.name):f for f in funcs}
    ranges = []
    for row in inventory:
        func = by_identity.get((row['addr'],row['name']))
        if func is None:
            ranges.append({'database':row,'status':'missing_elf_identity'})
        elif (row['size'],row['insn_count'],row['is_leaf']) != (func.size,len(func.insns),int(func.is_leaf)):
            ranges.append({'database':row,'expected':{'size':func.size,'insn_count':len(func.insns),'is_leaf':int(func.is_leaf)}})
    differences = []
    for key in sorted(prior.keys() | expected.keys()):
        old,new = prior.get(key),expected.get(key)
        if old == new:
            continue
        differences.append({'status':'missing' if old is None else 'extra' if new is None else 'changed',
            'address':key[0],'kind':key[1],'before':old,'after':new,
            'changed_fields':[f for f in FIELDS if old and new and old[f]!=new[f]]})
    return {'kind':'rom-bound-evidence-range-audit','database':str(db),'database_modified':False,
        'elf':str(elf),'elf_sha256':hashlib.sha256(elf.read_bytes()).hexdigest(),
        'extractor_sha256':hashlib.sha256(Path(evidence.__file__).read_bytes()).hexdigest(),
        'function_bindings':bindings,'inventory_differences':ranges,
        'evidence_counts':{'historical':len(prior),'regenerated':len(expected)},
        'difference_counts':dict(Counter(row['status'] for row in differences)),
        'evidence_differences':differences,'reference_c_read':False,
        'scope':'ROM identity and every decoded function word verified; regenerated base analysis remains conservative, not a semantic proof'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():
        raise ValueError('refusing to overwrite audit')
    report=audit(args.repo,args.db)
    from eval.agentrepair import _atomic_json
    _atomic_json(args.output,report)
    print(json.dumps({'functions':len(report['function_bindings']),
        'inventory_differences':len(report['inventory_differences']),
        'evidence_counts':report['evidence_counts'],'difference_counts':report['difference_counts']}))


if __name__=='__main__':
    main()
