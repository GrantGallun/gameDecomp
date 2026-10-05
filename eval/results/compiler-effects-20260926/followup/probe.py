"""One frozen, private second-edit probe after the prospective effect benchmark.

Stages: ``freeze``, then ``run``. Freeze reads the now-exposed evaluation pool,
chooses one compiler/front-end-valid gradient-improving child per function, and
stores up to 32 proposals from the existing full mutation stream. Run compiles
that fixed pool without search, promotion, or adaptive extensions. No reference
C or historical winning source is an input.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('effect_benchmark', HERE.parent / 'benchmark.py')
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)

from solver import regalloc_mutations, regalloc_signature, workspace

MAX_FUNCTIONS = 8
MAX_CHILDREN = 32
MAX_WORKERS = 3


def best_improving_child(baseline: dict, children: list[dict]) -> dict | None:
    before = baseline.get('gradient')
    if before is None:
        return None
    eligible = [child for child in children
                if child['compiled'] and child['frontend_passed'] is True
                and child['gradient'] is not None
                and tuple(child['gradient']) < tuple(before)]
    return min(eligible, key=lambda child: (tuple(child['gradient']),
                                            -child['score'], child['ordinal'])) if eligible else None


def frozen_file(run: Path, function: str, ordinal: int | None = None) -> Path:
    directory = run / 'followup' / 'frozen' / function
    return directory / ('parent.c' if ordinal is None else f'proposal-{ordinal:02d}.c')


def followup_manifest(run: Path) -> dict:
    manifest = benchmark.read_sealed(run / 'followup' / 'manifest.json')
    if manifest['benchmark_manifest_sha256'] != benchmark.digest((run / 'manifest.json').read_bytes()):
        raise ValueError('follow-up cohort bound to a different benchmark')
    for root in manifest['roots']:
        if benchmark.digest(frozen_file(run, root['function']).read_bytes()) != root['parent_sha256']:
            raise ValueError('follow-up parent source changed')
        for proposal in root['proposals']:
            if benchmark.digest(frozen_file(run, root['function'], proposal['ordinal']).read_bytes()) != proposal['source_sha256']:
                raise ValueError('follow-up proposal source changed')
    return manifest


def freeze(run: Path) -> None:
    out = run / 'followup'
    if out.exists():
        raise FileExistsError(out)
    base_manifest = benchmark.read_sealed(run / 'manifest.json')
    benchmark.check_code_pins(run, base_manifest)
    benchmark.read_sealed(run / 'report.json')  # completed evaluation outcome boundary
    roots = []
    for root in base_manifest['roots']:
        if root['split'] != 'evaluation':
            continue
        function = root['function']
        private = run / 'private' / function
        baseline = json.loads((private / 'result.json').read_text())['baseline']
        children = json.loads((private / 'children.json').read_text())['children']
        selected = best_improving_child(baseline, children)
        if selected is None:
            continue
        with sqlite3.connect((private / 'attempts.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT source_code,source_sha256,sampling,diff_summary,compiled '
                             'FROM attempts WHERE id=?', (selected['receipt_id'],)).fetchone()
        if row is None or row[4] != 1 or row[1] != selected['source_sha256'] or benchmark.digest(row[0]) != row[1]:
            raise ValueError(f'{function}: selected child receipt/source mismatch')
        source, source_sha, sampling_text, diff, _ = row
        sampling = json.loads(sampling_text or '{}')
        if (sampling.get('frontend') or {}).get('passed') is not True:
            raise ValueError(f'{function}: selected parent frontend not verified')
        evidence = {key: sampling.get(key) for key in
                    ('source_attribution', 'frontend', 'compiler_recipe')}
        proposals = []
        seen = {source_sha}
        for label, family, child in regalloc_mutations.variants(source, function,
                                                                 diff=diff or '', evidence=evidence):
            sha = benchmark.digest(child)
            if sha in seen:
                continue
            seen.add(sha)
            proposals.append({'ordinal': len(proposals), 'label': label,
                              'family': family, 'source_sha256': sha, 'source': child})
            if len(proposals) == MAX_CHILDREN:
                break
        roots.append({'function': function, 'tu': root['tu'], 'parent_receipt_id': selected['receipt_id'],
                      'parent_sha256': source_sha, 'parent_gradient': selected['gradient'],
                      'baseline_gradient': baseline['gradient'], 'parent_score': selected['score'],
                      'parent_source': source, 'proposals': proposals})
    if len(roots) != MAX_FUNCTIONS:
        raise RuntimeError(f'expected {MAX_FUNCTIONS} improving evaluation roots, found {len(roots)}')
    if any(not root['proposals'] for root in roots):
        raise RuntimeError('selected improving parent has no second-step proposals')
    out.mkdir()
    for root in roots:
        folder = frozen_file(run, root['function']).parent
        folder.mkdir(parents=True)
        frozen_file(run, root['function']).write_text(root.pop('parent_source'))
        for proposal in root['proposals']:
            frozen_file(run, root['function'], proposal['ordinal']).write_text(proposal.pop('source'))
    manifest = {'kind': 'compiler-effects-second-edit-v1',
                'benchmark_manifest_sha256': benchmark.digest((run / 'manifest.json').read_bytes()),
                'benchmark_report_sha256': benchmark.digest((run / 'report.json').read_bytes()),
                'code_pins_sha256': benchmark.digest((run / 'code-pins.json').read_bytes()),
                'probe_sha256': benchmark.digest(Path(__file__).read_bytes()),
                'selection': 'one best frontend-valid gradient improvement per evaluation function; '
                             'gradient asc, score desc, original ordinal asc',
                'generator': 'regalloc_mutations.variants full stream, original order, 32 unique maximum',
                'training_eligible': False, 'regime': 'header-assisted',
                'roots': roots}
    benchmark.seal(out / 'manifest.json', manifest)
    print(json.dumps({'selected_roots': len(roots),
                      'frozen_proposals': sum(len(r['proposals']) for r in roots),
                      'manifest': str(out / 'manifest.json')}, sort_keys=True), flush=True)


def score_child(db, ws: Path, repo: Path, function: str, root: dict,
                proposal: dict, source: str) -> dict:
    tag = f"{function}_ce_followup_{proposal['ordinal']:02d}"
    start = time.perf_counter()
    args = {'strategy': 'compiler-effects-two-step:' + proposal['family'],
            'model': 'deterministic-existing-mutation-stream',
            'prompt': 'Frozen second-step composition from evaluation child; no adaptive search.',
            'run_id': 'compiler-effects-20260926:followup:' + function,
            'parent_attempt_id': root['parent_receipt_id'],
            'relation': 'prospective-second-edit', 'action': proposal['label'],
            'extra': {'training_eligible': False, 'header_assisted': True,
                      'followup_parent_sha256': root['parent_sha256'],
                      'frozen_source_sha256': proposal['source_sha256'],
                      'followup_manifest_sha256': root['followup_manifest_sha256']}}
    try:
        attempt = workspace.score(ws, repo, tag, source, conn=db, func=function, **args)
    except Exception as exc:
        attempt = workspace.Attempt(False, 0., False, '', f'{type(exc).__name__}: {exc}', '')
        workspace.record_attempt(db, function, source, attempt, **{
            **args, 'extra': {**args['extra'], 'infrastructure_failure': True}})
    candidate_asm = ws / f'{tag}_object_dump_normalized.s'
    target_asm = ws / 'target_object_dump_normalized.s'
    gradient = None
    if attempt.compiled and candidate_asm.is_file() and target_asm.is_file():
        gradient = list(regalloc_signature.compare(target_asm.read_text(),
                                                   candidate_asm.read_text()).gradient)
    return {'ordinal': proposal['ordinal'], 'label': proposal['label'],
            'family': proposal['family'], 'source_sha256': proposal['source_sha256'],
            'receipt_id': attempt.receipt_id, 'compiled': attempt.compiled,
            'score': attempt.score, 'exact': attempt.exact,
            'frontend_passed': (attempt.frontend or {}).get('passed'),
            'certificate_exact': (attempt.verification or {}).get('exact'),
            'gradient': gradient,
            'error': attempt.compiler_stderr,
            'wall_seconds': time.perf_counter() - start}


def run_function(run_text: str, root: dict, manifest_sha: str) -> dict:
    run = Path(run_text)
    function = root['function']
    private = run / 'private' / function
    output = run / 'followup' / 'results' / f'{function}.json'
    if output.exists():
        raise FileExistsError(output)
    db_path = private / 'attempts.sqlite'
    repo = private / 'repo'
    ws = repo / 'nonmatchings' / function
    source = frozen_file(run, function).read_text()
    with sqlite3.connect(db_path) as db:
        parent = db.execute('SELECT source_sha256 FROM attempts WHERE id=?',
                            (root['parent_receipt_id'],)).fetchone()
        if parent != (root['parent_sha256'],) or benchmark.digest(source) != root['parent_sha256']:
            raise ValueError('follow-up parent receipt/source changed')
        linked = {**root, 'followup_manifest_sha256': manifest_sha}
        children = []
        for proposal in root['proposals']:
            child_source = frozen_file(run, function, proposal['ordinal']).read_text()
            children.append(score_child(db, ws, repo, function, linked, proposal, child_source))
    report = {'function': function, 'parent_receipt_id': root['parent_receipt_id'],
              'parent_sha256': root['parent_sha256'], 'parent_gradient': root['parent_gradient'],
              'parent_score': root['parent_score'], 'children': children,
              'training_eligible': False}
    benchmark.write_once(output, report)
    return {'function': function, 'attempts': len(children),
            'exact': sum(c['exact'] and c['frontend_passed'] is True for c in children),
            'output': str(output)}


def run_probe(run: Path, workers: int) -> None:
    stage_start = time.perf_counter()
    if workers < 1 or workers > MAX_WORKERS:
        raise ValueError('workers must be 1..3')
    manifest = followup_manifest(run)
    benchmark.check_code_pins(run, benchmark.read_sealed(run / 'manifest.json'))
    if benchmark.digest((run / 'code-pins.json').read_bytes()) != manifest['code_pins_sha256']:
        raise ValueError('first-stage benchmark code pins changed')
    if benchmark.digest(Path(__file__).read_bytes()) != manifest['probe_sha256']:
        raise ValueError('follow-up probe code changed after freeze')
    manifest_sha = benchmark.digest((run / 'followup' / 'manifest.json').read_bytes())
    failures = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_function, str(run), root, manifest_sha): root
                   for root in manifest['roots']}
        for future in concurrent.futures.as_completed(futures):
            root = futures[future]
            try:
                print(json.dumps(future.result(), sort_keys=True), flush=True)
            except Exception as exc:
                failures.append({'function': root['function'], 'error': f'{type(exc).__name__}: {exc}'})
                print(json.dumps(failures[-1], sort_keys=True), flush=True)
    if failures:
        benchmark.write_once(run / 'followup' / 'worker-failures.json', failures)
        raise RuntimeError(f'{len(failures)} follow-up workers failed')
    rows = [json.loads((run / 'followup' / 'results' / f"{root['function']}.json").read_text())
            for root in manifest['roots']]
    exact = [{'function': row['function'], 'source_sha256': c['source_sha256'],
              'source_path': str(frozen_file(run, row['function'], c['ordinal'])),
              'receipt_id': c['receipt_id']} for row in rows for c in row['children']
             if c['exact'] and c['frontend_passed'] is True and c['certificate_exact'] is True]
    summary = {'kind': 'compiler-effects-second-edit-result-v1',
               'manifest_sha256': manifest_sha,
               'function_count': len(rows), 'attempt_count': sum(len(r['children']) for r in rows),
               'compiled_count': sum(c['compiled'] for r in rows for c in r['children']),
               'frontend_pass_count': sum(c['frontend_passed'] is True for r in rows for c in r['children']),
               'wall_seconds_sum': sum(c['wall_seconds'] for r in rows for c in r['children']),
               'elapsed_wall_seconds': time.perf_counter() - stage_start,
               'exact': exact, 'training_eligible': False}
    benchmark.seal(run / 'followup' / 'report.json', summary)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('freeze', 'run'))
    parser.add_argument('--run', type=Path,
                        default=Path('/home/grant/decomp/experiments/compiler-effects-20260926'))
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    if args.stage == 'freeze':
        freeze(args.run)
    else:
        run_probe(args.run, args.workers)


if __name__ == '__main__':
    main()
