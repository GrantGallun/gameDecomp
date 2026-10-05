"""Reproduce a local, compiler-derived corpus; never calls a model or edits a solver KB.

Run in WSL: python3 .../build.py --out /home/grant/decomp/public-pairs-20260921
The output directory must be new. Repositories and objects live on the WSL filesystem.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools import corpus_grabber as cg
from tools import n64_corpus
from solver.byte_certificate import certify


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def run(args, **kw):
    result = subprocess.run(args, capture_output=True, timeout=300, **kw)
    if result.returncode:
        raise RuntimeError(f'{args!r}: {result.stderr!r}')
    return result


def recipes():
    """Only normal game-code flags; exclude directories with other recipes."""
    allowed = {x['id']: x for x in n64_corpus.load_manifest(cg.ALLOWLIST)['repositories']}
    result = [cg.load_recipes()['dkr@pal.v80']]
    definitions = {
        'mk64': ('58cfcb022e10f83bc3b889d7e97508cae6837098', 'eu.v11',
                 ['VERSION_EU=1', 'VERSION_EU_V11=1', 'F3DEX_GBI=1', 'F3D_OLD=1'],
                 ['include', 'build/eu.v11', 'build/eu.v11/include', 'src', 'src/racing', 'src/ending', '.', 'include/libc'],
                 ['/audio/', '/os/', '/debug/', '/data/', '/buffers/', '/ending/credits.c',
                  '/cpu_vehicles_camera_path.c', '/menu_items.c'],
                 ['-woff', '838,649,807', '-fullwarn']),
        'sm64': ('9921382a68bb0c865e5e45eb594d9c64db59b1af', 'eu',
                 ['VERSION_EU=1', 'F3D_NEW=1', '_FINALROM=1'],
                 ['include', 'build/eu', 'build/eu/include', 'src', '.', 'include/libc'],
                 ['/audio/', '/goddard/'], ['-Xfullwarn']),
    }
    for name, (commit, variant, defines, includes, exclusions, warnings) in definitions.items():
        local = ROOT / '.tools/corpus' / name
        assert run(['git', '-C', str(local), 'rev-parse', 'HEAD'], text=True).stdout.strip() == commit
        makefile = run(['git', '-C', str(local), 'show', f'{commit}:Makefile']).stdout
        flags = ['-G', '0', '-O2', '-nostdinc', '-DTARGET_N64', '-D_LANGUAGE_C', '-mips2']
        flags += [f'-I{x}' for x in includes] + [f'-D{x}' for x in defines]
        flags += ['-non_shared', '-Wab,-r4300_mul', '-Xcpluscomm', '-signed', '-32'] + warnings
        result.append({**allowed[name], 'commit': commit, 'variant': variant, 'key': f'{name}@{variant}',
                       'compiler': 'ido-5.3', 'flags': flags, 'source_roots': ['src'],
                       'exclude_path_patterns': exclusions, 'per_file_flags': [],
                       'makefile_sha256': sha(makefile.replace(b'\r\n', b'\n')),
                       'recipe_scope': 'Normal game C only; alternate compiler/optimization/encoding directories excluded.',
                       'recipe_evidence': 'Pinned Makefile version defines, Compiler Options, and per-file overrides.'})
    return result


def checkout(entry, dest):
    """Fetch just the pinned public commit; local caches may be partial clones."""
    cg.admissible(entry['url'])
    run(['git', 'init', '--quiet', str(dest)])
    run(['git', '-C', str(dest), 'remote', 'add', 'origin', entry['url']])
    run(['git', '-C', str(dest), 'fetch', '--quiet', '--depth=1', 'origin', entry['commit']])
    run(['git', '-C', str(dest), 'checkout', '--quiet', '--detach', entry['commit']])
    assert sha((dest / 'Makefile').read_bytes().replace(b'\r\n', b'\n')) == entry['makefile_sha256']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    if str(out).startswith('/mnt/'):
        raise SystemExit('Build artifacts must be on WSL ext4, not a Windows mount')
    out.mkdir(parents=True, exist_ok=False)
    os.nice(10)
    index = cg.EvaluationIndex.build()
    entries = recipes()
    dump(out / 'recipes.json', entries)
    compiler = cg.TOOLCHAINS['ido-5.3']
    compiler_files = {p.name: sha(p.read_bytes()) for p in compiler.parent.iterdir() if p.is_file()}
    manifest = {'schema_version': 1, 'started_at': int(time.time()), 'paid_api_calls': 0,
                'verification': 'Compiler-derived assembly/source pairs; not ROM-verified',
                'evaluation_functions_indexed': index.functions, 'compiler_path': str(compiler),
                'compiler_files_sha256': compiler_files, 'repositories': [],
                'guard': {'exact': True, 'near_duplicate_threshold': cg.NEAR_DUPLICATE,
                          'near_duplicate_min_tokens': cg.MIN_TOKENS_FOR_SIMILARITY,
                          'limitations': 'Heuristic source overlap guard, not proof of absence of contamination'},
                'context_policy': 'Checkouts and full translation units are verifier-only. Never serialize full source trees as model input.'}
    all_rows = []
    for entry in entries:
        key = entry['key'].replace('@', '.')
        repo = out / 'checkouts' / entry['id']
        repo.parent.mkdir(exist_ok=True)
        print(json.dumps({'event': 'checkout', 'repository': key}), flush=True)
        checkout(entry, repo)
        objects = out / 'objects' / key
        objects.mkdir(parents=True)
        builds = {}
        receipt = {'repository': entry['id'], 'variant': entry['variant'], 'commit': entry['commit'],
                   'files_considered': 0, 'files_compiled': 0, 'files_failed': [],
                   'files_skipped_rom_assembly': [], 'functions_seen': 0, 'functions_without_code': 0,
                   'guard_rejected': {}, 'per_file_rules_fired': {}, 'pairs': 0,
                   'replay_passed': 0, 'replay_rejected': [], 'exclusions': entry['exclude_path_patterns']}
        raw_compile = cg.compile_file

        def capture_compile(repo, entry, path, work):
            obj, stderr = raw_compile(repo, entry, path, work)
            if obj is not None:
                rel = path.relative_to(repo).as_posix()
                includes = [arg for p in cg.generated_include_dirs(entry) for arg in ('-I', str(p))]
                builds[rel] = {'source_file': str(path), 'source_sha256': sha(path.read_bytes()),
                               'object': str(obj.relative_to(out)), 'object_sha256': sha(obj.read_bytes()),
                               'cwd': str(repo), 'compiler': str(compiler),
                               'flags': includes + entry['flags'] + cg.per_file_flags(entry, rel),
                               'generated_headers': cg.generated_headers(entry)}
            return obj, stderr

        cg.compile_file = capture_compile
        rows = []
        try:
            for path in cg.source_files(repo, entry):
                receipt['files_considered'] += 1
                rows.extend(cg.pairs_for_file(repo, entry, path, index, objects, receipt))
                if receipt['files_considered'] % 10 == 0:
                    print(json.dumps({'event': 'compile', 'repository': key,
                                      'files': receipt['files_considered'], 'pairs': len(rows)}), flush=True)
        finally:
            cg.compile_file = raw_compile
        # Replay every retained TU independently, checking allocated data and relocations too.
        retained_files = sorted({r['file'] for r in rows})
        bad = set()
        replay_dir = out / 'replay' / key
        replay_dir.mkdir(parents=True)
        for rel in retained_files:
            build = builds[rel]
            candidate, stderr = raw_compile(repo, entry, repo / rel, replay_dir)
            cert = certify(out / build['object'], candidate, source=(repo / rel).read_text(),
                           build_inputs={'recipe': sha(json.dumps(entry, sort_keys=True).encode()),
                                         'source': build['source_sha256']}) if candidate else {'exact': False, 'stderr': stderr}
            cert_path = out / 'certificates' / key / (sha(rel.encode())[:16] + '.json')
            dump(cert_path, cert)
            build['replay_certificate'] = str(cert_path.relative_to(out))
            if cert['exact']:
                receipt['replay_passed'] += 1
            else:
                bad.add(rel)
                receipt['replay_rejected'].append({'file': rel, 'status': cert.get('status')})
        for row in rows:
            if row['file'] in bad:
                continue
            row.update(verification='compiler-derived; replay-certified object sections and relocations; not ROM-verified',
                       upstream_url=entry['url'], object=builds[row['file']]['object'],
                       context_ref=f'builds/{key}.json', recipe_ref='recipes.json',
                       id=f"{key}:{row['file']}:{row['function']}")
            all_rows.append(row)
            receipt['pairs'] += 1
        dump(out / 'builds' / f'{key}.json', builds)
        dump(out / 'receipts' / f'{key}.json', receipt)
        manifest['repositories'].append(receipt)
        print(json.dumps({'event': 'finished_repository', 'repository': key, 'pairs': receipt['pairs'],
                          'compiled': receipt['files_compiled'], 'failed': len(receipt['files_failed']),
                          'guard_rejected': receipt['guard_rejected'], 'replay_passed': receipt['replay_passed']}), flush=True)
    # Remove normalized duplicate implementations; keep each translation unit in one partition.
    seen = set()
    duplicates = []
    counts = collections.Counter()
    with (out / 'pairs.jsonl').open('w') as handle:
        for row in all_rows:
            fingerprint = cg._fingerprint(row['source'])[0]
            if fingerprint in seen:
                duplicates.append(row['id'])
                continue
            seen.add(fingerprint)
            group = f"{row['repository']}:{row['file']}"
            row['split'] = 'dev' if int(sha(group.encode())[:8], 16) % 10 == 0 else 'train'
            row['split_group'] = group
            row['normalized_source_sha256'] = fingerprint
            handle.write(json.dumps(row) + '\n')
            counts[f"{row['repository']}:{row['split']}"] += 1
    manifest.update(finished_at=int(time.time()), counts=dict(counts), deduplicated=len(duplicates),
                    pairs_sha256=sha((out / 'pairs.jsonl').read_bytes()),
                    pairs=sum(counts.values()), split_policy='Source-file grouped 90/10 train/dev; no sealed test or promotion evidence')
    dump(out / 'duplicates.json', duplicates)
    dump(out / 'manifest.json', manifest)
    print(json.dumps({'event': 'complete', 'pairs': manifest['pairs'], 'counts': dict(counts), 'out': str(out)}), flush=True)


if __name__ == '__main__':
    main()
