"""Make bounded, artificial repair exercises from public-source leaf functions.

These are curriculum examples, NOT observed solver repairs or evidence of transfer.
Every target and answer is compiled separately; mutated parents must compile and differ.
"""
from __future__ import annotations
import argparse
import collections
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools import corpus_grabber as cg, n64_corpus
from tools.synthetic_corpus import function_listing, OBJDUMP
from solver.byte_certificate import certify
from eval.repair_prompts import synthetic_repair_prompt, load_task_examples
from eval.repair_dataset_synth import leakage_check_text, sha_text

PRELUDE = '''typedef signed char s8;
typedef unsigned char u8;
typedef short s16;
typedef unsigned short u16;
typedef int s32;
typedef unsigned int u32;
typedef long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
'''


def compile_source(source, directory, flags, compiler, function):
    directory.mkdir(parents=True)
    path = directory / 'unit.c'
    path.write_text(source)
    obj = directory / 'unit.o'
    command = [str(compiler), '-c', *flags, '-o', str(obj), str(path)]
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=30)
    record = {'compiled': result.returncode == 0, 'stderr': result.stderr[-2000:],
              'command': command, 'object_path': str(obj), 'source_sha256': sha_text(source)}
    if record['compiled']:
        dis = subprocess.run([OBJDUMP, '-dr', '--no-show-raw-insn', str(obj)],
                             capture_output=True, text=True, check=True, timeout=30).stdout
        record['asm'] = '\n'.join(function_listing(dis, function))
    (directory / 'compile.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def mutation(source):
    masked = n64_corpus._mask_noncode(source)
    brace = masked.find('{')
    calls = set(re.findall(r'\b([A-Za-z_]\w*)\s*\(', masked[brace:]))
    if calls - {'if', 'while', 'for', 'switch', 'sizeof', 's8', 'u8', 's16', 'u16', 's32', 'u32', 'f32', 'f64'}:
        return None  # No invented prototypes, global call context or ABI assumptions.
    pattern = r'(?<![\w.])(?:0[xX][0-9a-fA-F]+|[0-9]+)(?![\w.])'
    for match in re.finditer(pattern, masked[brace:]):
        start, end = brace + match.start(), brace + match.end()
        before = masked[max(brace, start - 15):start]
        if not re.search(r'(?:return\s+|[+\-<>!=]=?\s*|<<\s*|>>\s*)$', before):
            continue
        value = int(match.group(), 16 if match.group().lower().startswith('0x') else 10)
        if not 0 <= value < 255:
            continue
        changed = source[:start] + str(value + 1) + source[end:]
        return changed
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', type=Path, required=True)
    ap.add_argument('--per-repository', type=int, default=24)
    ap.add_argument('--max-attempts', type=int, default=240)
    args = ap.parse_args()
    os.nice(10)
    out = args.corpus / 'repairs'
    out.mkdir(exist_ok=False)
    rows = [json.loads(x) for x in (args.corpus / 'pairs.jsonl').read_text().splitlines()]
    # Interleave repositories so the explicit global cap cannot starve later sources.
    by_repo = collections.defaultdict(list)
    for row in rows:
        if len(row['source']) <= 1800:
            by_repo[row['repository']].append(row)
    interleaved = []
    for number in range(max(map(len, by_repo.values()))):
        for group in by_repo.values():
            if number < len(group):
                interleaved.append(group[number])
    counts, rejected = collections.Counter(), collections.Counter()
    tasks, attempts = [], []
    index = cg.EvaluationIndex.build()
    cc = cg.TOOLCHAINS['ido-5.3']
    for row in interleaved:
        repo = row['repository']
        if counts[repo] >= args.per_repository or len(attempts) >= args.max_attempts:
            continue
        damaged = mutation(row['source'])
        if damaged is None:
            continue
        clean = PRELUDE + '\n' + row['source'] + '\n'
        parent = PRELUDE + '\n' + damaged + '\n'
        taskdir = out / 'objects' / sha_text(row['id'])[:16]
        flags = ['-G', '0', '-O2', '-mips1' if repo == 'dkr' else '-mips2', '-non_shared',
                 '-Wab,-r4300_mul', '-Xcpluscomm', '-fullwarn', '-nostdinc']
        if repo != 'dkr':
            flags += ['-signed', '-32']
        attempt = {'origin': row['id'], 'directory': str(taskdir), 'flags': flags}
        attempts.append(attempt)
        try:
            target = compile_source(clean, taskdir / 'target', flags, cc, row['function'])
            if not target['compiled'] or not target.get('asm'):
                attempt['rejected'] = 'target_not_self_contained'
                continue
            candidate = compile_source(parent, taskdir / 'parent', flags, cc, row['function'])
            if not candidate['compiled'] or not candidate.get('asm'):
                attempt['rejected'] = 'parent_compile_failure'
                continue
            before = certify(Path(target['object_path']), Path(candidate['object_path']), source=parent)
            if before['status'] != 'object_sections_differ':
                attempt['rejected'] = 'mutation_no_effect_or_uncertifiable'
                continue
            child = compile_source(clean, taskdir / 'child', flags, cc, row['function'])
            after = certify(Path(target['object_path']), Path(child['object_path']), source=clean)
            if not child['compiled'] or not after['exact']:
                attempt['rejected'] = 'child_replay_failed'
                continue
            diff = '\n'.join(difflib.unified_diff(candidate['asm'].splitlines(), target['asm'].splitlines(),
                                               fromfile='candidate assembly', tofile='target assembly', n=2))
            if not diff:
                attempt['rejected'] = 'no_instruction_difference'
                continue
            feedback = 'Candidate compiles. Its allocated sections/relocations differ from the target.\n' + diff
            prompt = synthetic_repair_prompt(asm=target['asm'], candidate=parent, feedback=feedback)
            leak = leakage_check_text(answer=clean, candidate=parent, visible=prompt)
            if not leak['clean']:
                attempt['rejected'] = 'answer_leakage'
                continue
            definitions = list(n64_corpus.extract_functions(clean))
            if len(definitions) != 1 or index.verdict(definitions[0]['body']) != 'clean':
                attempt['rejected'] = 'evaluation_overlap'
                continue
            certificate_path = taskdir / 'certificates.json'
            certificate_path.write_text(json.dumps({'parent': before, 'child': after}, indent=2) + '\n')
            task = {'schema_version': 1, 'task_id': 'public-mutation:' + row['id'],
                    'function': row['function'], 'family': 'public-leaf-immediate',
                    'source_kind': 'public-source-artificial-mutation', 'mutation': 'integer-immediate-plus-one',
                    'group': row['split_group'], 'split': row['split'],
                    'input': {'assembly': target['asm'], 'candidate': parent, 'feedback': {'text': feedback}},
                    'parent': {'compiled': True, 'exact': False, 'certificate_status': before['status']},
                    'child': {'exact': True, 'source_c': clean,
                              'certificate': {k: after[k] for k in ('exact', 'status', 'scope', 'kind', 'excluded')}},
                    'target': {'exactness_scope': after['scope'], 'whole_rom_verified': False},
                    'provenance': {'repository': repo, 'url': row['upstream_url'], 'commit': row['commit'],
                                   'file': row['file'], 'origin_pair': row['id'], 'flags': flags,
                                   'certificate_path': str(certificate_path), 'leakage': leak,
                                   'scope': 'Recompiled in minimal standalone type context; not original ROM/TU bytes or a real solver trajectory'}}
            tasks.append(task)
            attempt['accepted'] = True
            counts[repo] += 1
            print(json.dumps({'accepted': len(tasks), 'repository': repo, 'function': row['function']}), flush=True)
        finally:
            if 'rejected' in attempt:
                rejected[attempt['rejected']] += 1
            (out / 'attempts.json').write_text(json.dumps(attempts, indent=2) + '\n')
    (out / 'tasks.jsonl').write_text(''.join(json.dumps(t) + '\n' for t in tasks))
    examples = load_task_examples(tasks)
    assert len(examples) == len(tasks)
    report = {'accepted': len(tasks), 'by_repository': dict(counts), 'attempts': len(attempts),
              'rejected': dict(rejected), 'by_split': dict(collections.Counter(t['split'] for t in tasks)),
              'max_attempts': args.max_attempts, 'per_repository_cap': args.per_repository,
              'paid_api_calls': 0, 'trainer_loader_accepted': len(examples),
              'data_sha256': sha_text((out / 'tasks.jsonl').read_text()),
              'note': 'Artificial immediate-repair curriculum. No claim of real-game transfer or recursive improvement.'}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
