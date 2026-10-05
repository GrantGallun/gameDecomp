"""Audit the staged corpus, filter cross-split overlap and publish local data files.

Input staging artifacts are immutable. Only a NEW delivery directory is written.
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools import corpus_grabber as cg, n64_corpus
from tools.synthetic_corpus import function_listing, OBJDUMP
from solver.byte_certificate import certify
from eval.repair_prompts import load_task_examples, synthetic_repair_prompt
from eval.repair_dataset_synth import leakage_check_text


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--corpus', type=Path, required=True)
    ap.add_argument('--delivery', type=Path, required=True)
    args = ap.parse_args()
    corpus, delivery = args.corpus, args.delivery
    delivery.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((corpus / 'manifest.json').read_text())
    assert sha((corpus / 'pairs.jsonl').read_bytes()) == manifest['pairs_sha256']
    rows = read_rows(corpus / 'pairs.jsonl')
    eval_index = cg.EvaluationIndex.build()
    train_index = cg.EvaluationIndex()
    excluded = []
    viable = []
    dumps = {}
    object_hashes = {}
    builds = {}
    for row in rows:
        if row['file'].endswith('.inc.c'):
            excluded.append({'id': row['id'], 'reason': 'include_fragment_not_original_translation_unit'})
            continue
        assert row['repository'] in {'dkr', 'mk64', 'sm64'}
        assert sha(row['source'].encode()) == row['source_sha256']
        assert sha(row['asm'].encode()) == row['asm_sha256']
        functions = list(n64_corpus.extract_functions(row['source']))
        assert len(functions) == 1 and functions[0]['name'] == row['function']
        assert eval_index.verdict(functions[0]['body']) == 'clean'
        ref = row['context_ref']
        if ref not in builds:
            builds[ref] = json.loads((corpus / ref).read_text())
        build = builds[ref][row['file']]
        if row['object'] not in object_hashes:
            obj = corpus / row['object']
            object_hashes[row['object']] = sha(obj.read_bytes())
            dumps[row['object']] = subprocess.run([OBJDUMP, '-dr', '--no-show-raw-insn', str(obj)],
                                                   capture_output=True, text=True, check=True).stdout
            assert sha(Path(build['source_file']).read_bytes()) == build['source_sha256']
            cert = json.loads((corpus / build['replay_certificate']).read_text())
            replay_obj = corpus / 'replay' / obj.parent.name / obj.name
            assert cert['exact'] and sha(replay_obj.read_bytes()) == cert['candidate_sha256']
            assert cert['target_sha256'] == object_hashes[row['object']]
        assert object_hashes[row['object']] == build['object_sha256']
        assert '\n'.join(function_listing(dumps[row['object']], row['function'])) == row['asm']
        viable.append(row)
        if row['split'] == 'train':
            train_index.add(row['source'])
    contaminated_dev_groups = {row['split_group'] for row in viable
                               if row['split'] == 'dev' and train_index.verdict(row['source']) != 'clean'}
    final = []
    for row in viable:
        if row['split_group'] in contaminated_dev_groups:
            excluded.append({'id': row['id'], 'reason': 'dev_source_file_has_training_near_duplicate'})
        else:
            final.append(row)
    by_id = {row['id']: row for row in final}
    repairs = []
    for task in read_rows(corpus / 'repairs/tasks.jsonl'):
        origin = task['provenance']['origin_pair']
        if origin not in by_id:
            excluded.append({'id': task['task_id'], 'reason': 'origin_pair_excluded'})
            continue
        assert task['split'] == by_id[origin]['split']
        certpath = Path(task['provenance']['certificate_path'])
        taskdir = certpath.parent
        certificates = json.loads(certpath.read_text())
        target, child, parent = (taskdir / part / 'unit.o' for part in ('target', 'child', 'parent'))
        clean = task['child']['source_c']
        assert (taskdir / 'child/unit.c').read_text() == clean
        assert (taskdir / 'parent/unit.c').read_text() == task['input']['candidate']
        assert certificates['child']['candidate_sha256'] == sha(child.read_bytes())
        assert certify(target, child, source=clean)['exact']
        assert certify(target, parent, source=task['input']['candidate'])['status'] == 'object_sections_differ'
        prompt = synthetic_repair_prompt(asm=task['input']['assembly'], candidate=task['input']['candidate'],
                                         feedback=task['input']['feedback']['text'])
        assert leakage_check_text(answer=clean, candidate=task['input']['candidate'], visible=prompt)['clean']
        repairs.append(task)
    assert len(load_task_examples(repairs)) == len(repairs)
    write_rows(delivery / 'assembly-source.jsonl', final)
    write_rows(delivery / 'repair-tasks.jsonl', repairs)
    write_rows(delivery / 'excluded.jsonl', excluded)
    for source in ('recipes.json', 'manifest.json', 'duplicates.json'):
        shutil.copy2(corpus / source, delivery / ('harvest-' + source))
    for folder in ('builds', 'receipts'):
        shutil.copytree(corpus / folder, delivery / folder)
    shutil.copy2(corpus / 'repairs/report.json', delivery / 'repair-generation-report.json')
    # Retain extracted headers needed to replay the DKR recipe, verifying the build-time hash.
    generated = {}
    for build_file in builds.values():
        for build in build_file.values():
            for header in build['generated_headers']:
                path = Path(header['path'])
                assert sha(path.read_bytes()) == header['sha256']
                generated[str(path)] = header['sha256']
                dest = delivery / 'generated-headers' / header['sha256'] / path.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    summary = {'schema_version': 1, 'status': 'verified', 'compiler_derived_pairs': len(final),
               'pairs_by_repository': dict(collections.Counter(r['repository'] for r in final)),
               'pairs_by_split': dict(collections.Counter(r['split'] for r in final)),
               'pairs_by_repository_and_split': dict(collections.Counter(f"{r['repository']}:{r['split']}" for r in final)),
               'repair_tasks': len(repairs),
               'repairs_by_repository': dict(collections.Counter(r['provenance']['repository'] for r in repairs)),
               'repairs_by_split': dict(collections.Counter(r['split'] for r in repairs)),
               'excluded_by_reason': dict(collections.Counter(r['reason'] for r in excluded)),
               'raw_pairs': len(rows), 'objects_revalidated': len(object_hashes),
               'eval_functions_indexed': eval_index.functions,
               'guard_rejections_at_harvest': {r['repository']: r['guard_rejected'] for r in manifest['repositories']},
               'raw_normalized_duplicates_removed': manifest['deduplicated'],
               'split_policy': 'File-grouped; entire dev files with exact/near train overlap excluded; repairs inherit source split.',
               'guard_limit': 'Normalized exact + heuristic near duplicate detection; does not prove all leakage absent.',
               'verification': 'Independently recompiled object sections + relocations; no whole-ROM verification.',
               'repair_scope': 'Artificial immediate mutations in standalone type context, not observed solver repairs.',
               'paid_api_calls': 0, 'model_training_started': False,
               'wsl_artifacts': str(corpus), 'generated_header_hashes': generated,
               'files_sha256': {name: sha((delivery / name).read_bytes()) for name in
                                ('assembly-source.jsonl', 'repair-tasks.jsonl')}}
    (delivery / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
