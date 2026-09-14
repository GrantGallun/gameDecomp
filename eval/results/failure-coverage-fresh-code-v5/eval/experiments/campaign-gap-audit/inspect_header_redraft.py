"""Preserve an assembly/header-only diagnostic redraft; no reference C bodies."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from eval import agentrepair
from solver import m2c_input


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--function', required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    root = Path('/mnt/c/Code/gameDecomp')
    agentrepair._refuse_frozen_heldout(root/'eval/sets', args.function)
    if not re.fullmatch(r'[A-Za-z_]\w*', args.function):
        raise ValueError('invalid function')
    draft_path = args.out.with_suffix('.generated.c')
    if args.out.exists() or draft_path.exists():
        raise ValueError('refusing overwrite')
    repo = Path('/home/grant/decomp/sbk1')
    target = repo/'nonmatchings'/args.function/'target.s'
    source = args.source.read_text()
    headers = tuple(dict.fromkeys(['common.h', *re.findall(r'(?m)^\s*#include "([^"]+)"', source)]))
    result, meta = m2c_input.draft(repo,target,context_headers=headers,valid_syntax=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    with draft_path.open('x', encoding='utf-8') as stream:
        stream.write(result.stdout)
    lines = result.stdout.splitlines()
    contexts = [{'line': i+1, 'context': lines[max(0,i-1):i+2]}
                for i, line in enumerate(lines) if 'M2C_UNK' in line]
    report = {'function': args.function, 'generated_path': str(draft_path),
        'input_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'target_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'generated_sha256': hashlib.sha256(result.stdout.encode()).hexdigest(),
        'draft': meta, 'unknown_contexts': contexts,
        'reference_bodies_used': False, 'model_calls': 0}
    agentrepair._atomic_json(args.out, report)
    print(json.dumps(contexts[:15]))


if __name__ == '__main__':
    main()
