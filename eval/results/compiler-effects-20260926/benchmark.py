"""Prospective, source-bound compiler-effect benchmark.

Run with the WSL Python environment used by the native SBK1 repository. Stages
are deliberately separate: freeze, development compiles, fit, evaluation
baselines/rankings, evaluation compiles, report. The evaluation ranking is
sealed before any evaluation child is compiled.

Only retained candidate C, its recorded diff and compiler evidence, and the
original binary's target object enter this experiment. No reference C or prior
winning candidate is read. All private receipts are training-ineligible.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.campaign_state import read as read_campaign
from eval.campaign_workers import isolate
from solver import regalloc_mutations, regalloc_signature, workspace

FAMILIES = frozenset({'local_web_merge', 'pure_inline', 'stmt_move', 'stmt_order'})
EXPOSED_CONTROLS = frozenset({
    'releaseSoundEffectHandleNode', 'audioThreadMain',
    'drawControllerPakFileDeleteConfirmOptions',
})
SEED = 'compiler-effects-prospective-20260926-v1'
MAX_ROOTS = 40
MAX_PROPOSALS = 16
MAX_WORKERS = 3


def digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=True) + '\n').encode()


def write_once(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(canonical(value))


def read_sealed(path: Path) -> dict:
    data = path.read_bytes()
    expected = path.with_name(path.name + '.sha256').read_text().strip()
    if digest(data) != expected:
        raise ValueError(f'seal mismatch: {path}')
    return json.loads(data)


def seal(path: Path, value: object) -> str:
    data = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)
    sha = digest(data)
    path.with_name(path.name + '.sha256').write_text(sha + '\n')
    return sha


def db_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)


def source_file(run: Path, root: dict, proposal: dict | None = None) -> Path:
    directory = run / 'frozen' / root['function']
    return directory / ('parent.c' if proposal is None else f"proposal-{proposal['ordinal']:02d}.c")


def choose_split(roots: list[dict]) -> None:
    """Assign complete translation units, seeking 16 development roots."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for root in roots:
        groups[root['tu']].append(root)
    ordered = sorted(groups, key=lambda tu: (digest(SEED + tu), tu))
    dev: set[str] = set()
    count = 0
    for tu in ordered:
        size = len(groups[tu])
        if count + size <= 16 or (count < 16 and abs(16 - (count + size)) < abs(16 - count)):
            dev.add(tu)
            count += size
    if not dev or len(dev) == len(groups):
        raise RuntimeError('cohort does not permit a TU-disjoint development/evaluation split')
    for root in roots:
        root['split'] = 'development' if root['tu'] in dev else 'evaluation'


def frozen_proposals(source: str, function: str, diff: str, sampling: dict) -> list[dict]:
    evidence = {key: sampling.get(key) for key in
                ('source_attribution', 'frontend', 'compiler_recipe')}
    proposals = []
    seen = {digest(source)}
    for label, family, child in regalloc_mutations.variants(
            source, function, diff=diff, evidence=evidence):
        if family not in FAMILIES:
            continue
        sha = digest(child)
        if sha in seen:
            continue
        seen.add(sha)
        proposals.append({'ordinal': len(proposals), 'label': label,
                          'family': family, 'source_sha256': sha, 'source': child})
        if len(proposals) == MAX_PROPOSALS:
            break
    return proposals


def compiler_domain(recipe: dict | None, repo: Path) -> dict:
    """Use code-generation identity, excluding the function/TU target path."""
    if not recipe or not isinstance(recipe.get('settings'), dict):
        raise ValueError('baseline compiler recipe unavailable for domain identity')
    toolchain = toolchain_hashes(repo)
    identity = {'settings': {key: recipe['settings'].get(key) for key in
                             ('IDO_CC', 'CFLAGS', 'C_OPT', 'C_MIPS', 'ASFLAGS',
                              'C_OBJ_POSTPROCESS')},
                'makefile_sha256': recipe.get('makefile_sha256'),
                'toolchain_sha256': digest(canonical(toolchain))}
    if any(value is None for value in identity['settings'].values()) or not identity['makefile_sha256']:
        raise ValueError('incomplete compiler domain identity')
    return {'digest': digest(canonical(identity)), 'identity': identity}


@lru_cache(maxsize=4)
def toolchain_hashes(repo: Path) -> dict[str, str]:
    directory = repo / 'tools/ido-recomp/linux'
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    files = {path.name: digest(path.read_bytes()) for path in sorted(directory.iterdir())
             if path.is_file()}
    if 'cc' not in files:
        raise FileNotFoundError(directory / 'cc')
    return files


def select_roots(metadata: list[dict], proposal_builder, limit: int = MAX_ROOTS) -> tuple[list[dict], int]:
    """Same seeded/TU selection as exhaustive enumeration, with lazy proposals."""
    ordered = sorted(metadata, key=lambda r: (digest(SEED + r['function']), r['function']))
    chosen, per_tu, attempted = [], Counter(), set()
    for root in ordered:
        if per_tu[root['tu']] >= 2:
            continue
        attempted.add(root['function'])
        proposals = proposal_builder(root)
        if not proposals:
            continue
        chosen.append({**root, 'proposals': proposals})
        per_tu[root['tu']] += 1
        if len(chosen) == limit:
            return chosen, len(attempted)
    # Only needed if there are fewer than `limit` roots within the 2/TU cap.
    for root in ordered:
        if root['function'] in attempted:
            continue
        attempted.add(root['function'])
        proposals = proposal_builder(root)
        if proposals:
            chosen.append({**root, 'proposals': proposals})
            if len(chosen) == limit:
                break
    return chosen, len(attempted)


def freeze(args: argparse.Namespace) -> None:
    run = args.run
    if run.exists():
        raise FileExistsError(f'freeze directory already exists: {run}')
    state = read_campaign(args.campaign)
    campaign_pin = digest(args.campaign.read_bytes())
    metadata = []
    with db_ro(args.native_db) as native, db_ro(args.kb) as kb:
        for name, node in state['nodes'].items():
            if name in EXPOSED_CONTROLS or node.get('status') != 'pending':
                continue
            attempt_id = node.get('attempt_id')
            if not isinstance(attempt_id, int):
                continue
            row = native.execute(
                'SELECT source_code,source_sha256,compiled,exact,diff_summary,sampling '
                'FROM attempts WHERE id=?', (attempt_id,)).fetchone()
            if not row or row[2] != 1 or row[3] != 0:
                continue
            source, sha, _, _, diff, sampling_text = row
            if digest(source) != sha or sha != node.get('source_sha256'):
                continue
            sampling = json.loads(sampling_text or '{}')
            if (sampling.get('frontend') or {}).get('passed') is not True:
                continue
            meta = kb.execute('SELECT f.addr,t.name FROM functions f JOIN tus t ON '
                              't.id=f.tu_id WHERE f.name=?', (name,)).fetchone()
            if not meta or not (args.repo / 'nonmatchings' / name / 'build.sh').is_file():
                continue
            metadata.append({'function': name, 'tu': meta[1], 'addr': meta[0],
                             'native_attempt_id': attempt_id, 'source_sha256': sha,
                             'native_score': node.get('score'),
                             'native_diff_sha256': digest(diff or ''),
                             'parent_source': source, 'native_diff': diff or '',
                             'native_sampling': sampling})
    def proposals_for(root):
        return frozen_proposals(root['parent_source'], root['function'],
                                root['native_diff'], root['native_sampling'])
    chosen, proposal_checked = select_roots(metadata, proposals_for)
    if len(chosen) != MAX_ROOTS:
        raise RuntimeError(f'only {len(chosen)} eligible roots with proposals among '
                           f'{proposal_checked} checked parents; need {MAX_ROOTS}')
    choose_split(chosen)
    run.mkdir(parents=True)
    manifest_roots = []
    for root in chosen:
        folder = run / 'frozen' / root['function']
        folder.mkdir(parents=True)
        source_file(run, root).write_text(root.pop('parent_source'))
        root.pop('native_diff')
        root.pop('native_sampling')
        for proposal in root['proposals']:
            source_file(run, root, proposal).write_text(proposal.pop('source'))
        manifest_roots.append(root)
    manifest = {'kind': 'prospective-compiler-effects-v1', 'seed': SEED,
                'campaign': str(args.campaign), 'campaign_pointer_sha256': campaign_pin,
                'native_db': str(args.native_db), 'kb': str(args.kb),
                'repo': str(args.repo), 'training_eligible': False,
                'regime': 'header-assisted', 'families': sorted(FAMILIES),
                'max_proposals': MAX_PROPOSALS, 'roots': manifest_roots,
                'metadata_eligible_count': len(metadata),
                'proposal_checked_count': proposal_checked,
                'proposal_eligible_count': 'at least 40; enumeration stopped at frozen cohort',
                'split_counts': dict(Counter(r['split'] for r in chosen))}
    seal(run / 'manifest.json', manifest)
    print(json.dumps({'frozen': len(chosen), 'split': manifest['split_counts'],
                      'proposals': sum(len(r['proposals']) for r in chosen),
                      'run': str(run)}, sort_keys=True), flush=True)


def load_manifest(run: Path) -> dict:
    manifest = read_sealed(run / 'manifest.json')
    for root in manifest['roots']:
        if digest(source_file(run, root).read_bytes()) != root['source_sha256']:
            raise ValueError(f"frozen parent changed: {root['function']}")
        for proposal in root['proposals']:
            if digest(source_file(run, root, proposal).read_bytes()) != proposal['source_sha256']:
                raise ValueError(f"frozen proposal changed: {root['function']}/{proposal['ordinal']}")
    return manifest


CODE_INPUTS = (
    'eval/results/compiler-effects-20260926/benchmark.py',
    'eval/campaign_workers.py', 'eval/campaign_state.py',
    'kb/attempts.py',
)


def code_inputs(manifest: dict) -> dict[str, str]:
    files = {str(ROOT / relative): digest((ROOT / relative).read_bytes())
             for relative in CODE_INPUTS}
    for path in sorted((ROOT / 'solver').glob('*.py')):
        files[str(path)] = digest(path.read_bytes())
    repo = Path(manifest['repo'])
    for root in manifest['roots']:
        ws = repo / 'nonmatchings' / root['function']
        for path in ws.iterdir():
            if path.is_file() and (path.name in {'build.sh', 'prelude.inc',
                                                 '.compiler-target.json', '.diff_algorithm'}
                                   or path.name.startswith('target')):
                files[str(path)] = digest(path.read_bytes())
    files[str(repo / 'Makefile')] = digest((repo / 'Makefile').read_bytes())
    for name, sha in toolchain_hashes(repo).items():
        files[str(repo / 'tools/ido-recomp/linux' / name)] = sha
    return files


def pin_code(run: Path, manifest: dict) -> None:
    if (run / 'private').exists():
        raise RuntimeError('private scoring artifacts exist before code pin')
    pins = {'manifest_sha256': digest((run / 'manifest.json').read_bytes()),
            'files': code_inputs(manifest)}
    seal(run / 'code-pins.json', pins)
    print(json.dumps({'pinned_files': len(pins['files']),
                      'code_pins_sha256': digest((run / 'code-pins.json').read_bytes())}), flush=True)


def check_code_pins(run: Path, manifest: dict) -> None:
    pins = read_sealed(run / 'code-pins.json')
    if pins['manifest_sha256'] != digest((run / 'manifest.json').read_bytes()):
        raise ValueError('code pins bound to another manifest')
    current = code_inputs(manifest)
    if pins['files'] != current:
        changed = sorted(set(pins['files']) ^ set(current) |
                         {p for p in pins['files'] if p in current and pins['files'][p] != current[p]})
        raise ValueError('core benchmark/compiler inputs changed after pin: ' + ', '.join(changed))


def clone_lineage(private_db: Path, root: dict, manifest: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(private_db)
    conn.executescript((ROOT / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS kb', (Path(manifest['kb']).resolve().as_uri() + '?mode=ro',))
    function_row = conn.execute('SELECT * FROM kb.functions WHERE addr=?', (root['addr'],)).fetchone()
    if not function_row:
        raise RuntimeError(f"missing KB function {root['function']}")
    conn.execute('INSERT INTO tus SELECT * FROM kb.tus WHERE id=?', (function_row[2],))
    conn.execute('INSERT INTO functions SELECT * FROM kb.functions WHERE addr=?', (root['addr'],))
    conn.execute('ATTACH DATABASE ? AS native', (Path(manifest['native_db']).resolve().as_uri() + '?mode=ro',))
    ancestors = []
    attempt_id = root['native_attempt_id']
    while attempt_id is not None:
        row = conn.execute('SELECT id,parent_attempt_id,run_id FROM native.attempts WHERE id=?',
                           (attempt_id,)).fetchone()
        if row is None or row[0] in {item[0] for item in ancestors}:
            raise RuntimeError(f'broken native ancestry at {attempt_id}')
        ancestors.append(row)
        attempt_id = row[1]
    for attempt_id, _, run_id in reversed(ancestors):
        if run_id:
            conn.execute('INSERT OR IGNORE INTO attempt_runs SELECT * FROM native.attempt_runs WHERE id=?',
                         (run_id,))
        conn.execute('INSERT INTO attempts SELECT * FROM native.attempts WHERE id=?', (attempt_id,))
    row = conn.execute('SELECT source_sha256 FROM attempts WHERE id=?',
                       (root['native_attempt_id'],)).fetchone()
    if row != (root['source_sha256'],):
        raise RuntimeError('retained parent identity mismatch after lineage clone')
    conn.commit()
    return conn


def assembly(path: Path) -> str | None:
    return path.read_text() if path.is_file() else None


def gradient(target: str | None, candidate: str | None) -> list[int] | None:
    if target is None or candidate is None:
        return None
    return list(regalloc_signature.compare(target, candidate).gradient)


def score_one(conn: sqlite3.Connection, ws: Path, repo: Path, root: dict,
              source: str, tag: str, parent_id: int, action: str) -> dict:
    start = time.perf_counter()
    try:
        attempt = workspace.score(
            ws, repo, tag, source, conn=conn, func=root['function'],
            strategy='compiler-effects-prospective:' + action,
            model='deterministic-mechanistic-predictor',
            prompt='Frozen retained source/proposals; no reference C or winner input.',
            run_id='compiler-effects-20260926:' + root['function'],
            parent_attempt_id=parent_id, relation='prospective-benchmark', action=action,
            extra={'training_eligible': False, 'header_assisted': True,
                   'native_parent_attempt_id': root['native_attempt_id'],
                   'frozen_source_sha256': digest(source)})
    except Exception as exc:  # ordinary score should log compile failures; capture infrastructure failures too
        attempt = workspace.Attempt(False, 0.0, False, '',
                                    f'{type(exc).__name__}: {exc}', '')
        workspace.record_attempt(
            conn, root['function'], source, attempt,
            strategy='compiler-effects-prospective:' + action,
            model='deterministic-mechanistic-predictor',
            run_id='compiler-effects-20260926:' + root['function'],
            parent_attempt_id=parent_id, relation='prospective-benchmark', action=action,
            extra={'training_eligible': False, 'header_assisted': True,
                   'infrastructure_failure': True, 'frozen_source_sha256': digest(source)})
    elapsed = time.perf_counter() - start
    dump = assembly(ws / f'{tag}_object_dump_normalized.s') if attempt.compiled else None
    target = assembly(ws / 'target_object_dump_normalized.s')
    return {'receipt_id': attempt.receipt_id, 'compiled': attempt.compiled,
            'score': attempt.score, 'exact': attempt.exact,
            'frontend_passed': (attempt.frontend or {}).get('passed'),
            'certificate_exact': (attempt.verification or {}).get('exact'),
            'source_sha256': digest(source), 'error': attempt.compiler_stderr,
            'elapsed_seconds': elapsed, 'gradient': gradient(target, dump),
            'asm': dump, 'target_asm': target, 'diff': attempt.diff,
            'attribution': attempt.source_attribution,
            'compiler_recipe': attempt.compiler_recipe}


def effect_module():
    from solver import compiler_effects
    return compiler_effects


def root_paths(run: Path, root: dict) -> tuple[Path, Path]:
    base = run / 'private' / root['function']
    return base, base / 'result.json'


def run_root(run_text: str, manifest: dict, root: dict, include_children: bool) -> dict:
    run = Path(run_text)
    base, result_path = root_paths(run, root)
    if result_path.exists():
        raise FileExistsError(f'result already exists: {result_path}')
    repo = isolate(Path(manifest['repo']), base / 'repo', root['function'])
    ws = repo / 'nonmatchings' / root['function']
    conn = clone_lineage(base / 'attempts.sqlite', root, manifest)
    source = source_file(run, root).read_text()
    baseline = score_one(conn, ws, repo, root, source,
                         root['function'] + '_ce_parent', root['native_attempt_id'], 'baseline')
    result = {'function': root['function'], 'group': root['tu'],
              'split': root['split'], 'baseline': baseline, 'children': [],
              'proposals': [], 'training_eligible': False}
    if baseline['compiled'] and baseline['frontend_passed'] is True and baseline['asm']:
        effects = effect_module()
        domain = compiler_domain(baseline['compiler_recipe'], repo)
        result['compiler_domain'] = domain
        before = effects.assembly_state(baseline['asm'])
        target_state = effects.assembly_state(baseline['target_asm'])
        result['before'] = before
        result['target_state'] = target_state
        for proposal in root['proposals']:
            child = source_file(run, root, proposal).read_text()
            start = time.perf_counter()
            feat = effects.features(source, child, root['function'], proposal['family'],
                                    baseline['diff'], baseline['attribution'], baseline['asm'])
            feat['domain'] = domain['digest']
            result['proposals'].append({**proposal, 'features': feat,
                                        'feature_seconds': time.perf_counter() - start})
    else:
        result['excluded_reason'] = 'fresh baseline did not compile and pass frontend'
    if include_children:
        for proposal in root['proposals']:
            child = source_file(run, root, proposal).read_text()
            receipt = score_one(conn, ws, repo, root, child,
                                root['function'] + f"_ce_{proposal['ordinal']:02d}",
                                baseline['receipt_id'], proposal['label'])
            receipt.update(ordinal=proposal['ordinal'], label=proposal['label'],
                           family=proposal['family'])
            if receipt['asm'] is not None:
                receipt['after'] = effect_module().assembly_state(receipt['asm'])
            result['children'].append(receipt)
    conn.close()
    write_once(result_path, result)
    return {'function': root['function'], 'split': root['split'],
            'baseline_compiled': baseline['compiled'], 'children': len(result['children']),
            'exact': sum(c['exact'] and c['frontend_passed'] is True for c in result['children'])}


def run_split(args: argparse.Namespace, manifest: dict, split: str,
              include_children: bool) -> None:
    roots = [r for r in manifest['roots'] if r['split'] == split]
    workers = min(args.workers, MAX_WORKERS)
    if args.workers < 1 or args.workers > MAX_WORKERS:
        raise ValueError('workers must be 1..3')
    failures = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_root, str(args.run), manifest, r, include_children): r
                   for r in roots}
        for future in concurrent.futures.as_completed(futures):
            root = futures[future]
            try:
                print(json.dumps(future.result(), sort_keys=True), flush=True)
            except Exception as exc:
                failures.append({'function': root['function'], 'error': f'{type(exc).__name__}: {exc}'})
                print(json.dumps(failures[-1], sort_keys=True), flush=True)
    if failures:
        write_once(args.run / f'{split}-failures.json', failures)
        raise RuntimeError(f'{len(failures)} function workers failed; private receipts retained')


def results_for(run: Path, roots: list[dict]) -> list[dict]:
    rows = []
    for root in roots:
        path = root_paths(run, root)[1]
        if not path.is_file():
            raise FileNotFoundError(path)
        rows.append(json.loads(path.read_text()))
    return rows


def fit_model(args: argparse.Namespace, manifest: dict) -> None:
    dev_roots = [r for r in manifest['roots'] if r['split'] == 'development']
    evaluation = [r for r in manifest['roots'] if r['split'] == 'evaluation']
    if any(root_paths(args.run, r)[1].exists() for r in evaluation):
        raise RuntimeError('evaluation baseline or child already scored before model fit')
    rows = []
    for result in results_for(args.run, dev_roots):
        before = result.get('before')
        features = {p['ordinal']: p['features'] for p in result['proposals']}
        for child in result['children']:
            if child['ordinal'] not in features:
                continue
            rows.append({'group': result['group'], 'features': features[child['ordinal']],
                         'before': before, 'after': child.get('after'),
                         'compiled': child['compiled'] and child['frontend_passed'] is True})
    development_groups = sorted({r['tu'] for r in dev_roots})
    started = time.perf_counter()
    model = effect_module().fit(rows, development_groups)
    elapsed = time.perf_counter() - started
    seal(args.run / 'model.json', {'model': model, 'development_groups': development_groups,
                                  'row_count': len(rows), 'fit_seconds': elapsed,
                                  'manifest_sha256': digest((args.run / 'manifest.json').read_bytes())})
    print(json.dumps({'fit_rows': len(rows), 'development_groups': len(development_groups),
                      'seconds': elapsed}), flush=True)


def ordered_ordinals(ranked: list, proposals: list[dict]) -> list[int]:
    originals = {p['ordinal'] for p in proposals}
    ordinals = [int(p['ordinal'] if isinstance(p, dict) else p) for p in ranked]
    if len(ordinals) != len(originals) or set(ordinals) != originals:
        raise ValueError('rank must be a permutation of the frozen proposal ordinals')
    return ordinals


def prepare_evaluation(args: argparse.Namespace, manifest: dict) -> None:
    model_record = read_sealed(args.run / 'model.json')
    if model_record['manifest_sha256'] != digest((args.run / 'manifest.json').read_bytes()):
        raise ValueError('model bound to another manifest')
    if (args.run / 'evaluation-rankings.json').exists():
        raise FileExistsError('evaluation rankings already sealed')
    run_split(args, manifest, 'evaluation', False)
    roots = [r for r in manifest['roots'] if r['split'] == 'evaluation']
    rankings = []
    for result in results_for(args.run, roots):
        if not result.get('before'):
            rankings.append({'function': result['function'], 'excluded_reason': result.get('excluded_reason')})
            continue
        proposals = [{'ordinal': p['ordinal'], 'features': p['features']}
                     for p in result['proposals']]
        started = time.perf_counter()
        ranked = effect_module().rank(model_record['model'], proposals,
                                      result['before'], result['target_state'])
        elapsed = time.perf_counter() - started
        rankings.append({'function': result['function'], 'group': result['group'],
                         'ordinals': ordered_ordinals(ranked, proposals),
                         'ranked': ranked, 'rank_seconds': elapsed,
                         'feature_seconds': sum(p['feature_seconds'] for p in result['proposals'])})
    seal(args.run / 'evaluation-rankings.json',
         {'manifest_sha256': model_record['manifest_sha256'],
          'model_sha256': digest((args.run / 'model.json').read_bytes()),
          'rankings': rankings})
    print(json.dumps({'ranked': sum('ordinals' in r for r in rankings),
                      'excluded_baselines': sum('ordinals' not in r for r in rankings)}), flush=True)


def score_evaluation_children(args: argparse.Namespace, manifest: dict) -> None:
    rankings = read_sealed(args.run / 'evaluation-rankings.json')
    if rankings['model_sha256'] != digest((args.run / 'model.json').read_bytes()):
        raise ValueError('ranking model changed')
    roots = [r for r in manifest['roots'] if r['split'] == 'evaluation']
    # Each function has an already scored baseline in its private DB. Child
    # scoring reopens that DB without rewriting the sealed baseline result.
    failures = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(score_existing_children, str(args.run), manifest, r): r for r in roots}
        for future in concurrent.futures.as_completed(futures):
            root = futures[future]
            try:
                print(json.dumps(future.result(), sort_keys=True), flush=True)
            except Exception as exc:
                failures.append({'function': root['function'], 'error': f'{type(exc).__name__}: {exc}'})
                print(json.dumps(failures[-1], sort_keys=True), flush=True)
    if failures:
        write_once(args.run / 'evaluation-child-failures.json', failures)
        raise RuntimeError(f'{len(failures)} child workers failed')


def score_existing_children(run_text: str, manifest: dict, root: dict) -> dict:
    run = Path(run_text)
    base, result_path = root_paths(run, root)
    original = json.loads(result_path.read_text())
    if original['children']:
        raise RuntimeError('evaluation children already present in baseline result')
    child_path = base / 'children.json'
    if child_path.exists():
        raise FileExistsError(child_path)
    repo = base / 'repo'
    ws = repo / 'nonmatchings' / root['function']
    with sqlite3.connect(base / 'attempts.sqlite') as conn:
        children = []
        for proposal in root['proposals']:
            child = source_file(run, root, proposal).read_text()
            receipt = score_one(conn, ws, repo, root, child,
                                root['function'] + f"_ce_{proposal['ordinal']:02d}",
                                original['baseline']['receipt_id'], proposal['label'])
            receipt.update(ordinal=proposal['ordinal'], label=proposal['label'],
                           family=proposal['family'])
            if receipt['asm'] is not None:
                receipt['after'] = effect_module().assembly_state(receipt['asm'])
            children.append(receipt)
    write_once(child_path, {'function': root['function'], 'children': children,
                            'training_eligible': False})
    return {'function': root['function'], 'children': len(children),
            'exact': sum(c['exact'] and c['frontend_passed'] is True for c in children)}


def _success(child: dict, baseline: dict, kind: str) -> bool:
    if child['frontend_passed'] is not True or not child['compiled']:
        return False
    if kind == 'exact':
        return child['exact'] and child['certificate_exact'] is True
    if kind == 'improvement':
        return child['score'] > baseline['score']
    if kind == 'fault_gradient':
        return (child['gradient'] is not None and baseline['gradient'] is not None
                and tuple(child['gradient']) < tuple(baseline['gradient']))
    raise ValueError(kind)


def replay(children: list[dict], baseline: dict, order: list[int]) -> dict:
    by_ordinal = {c['ordinal']: c for c in children}
    result = {}
    elapsed = [by_ordinal[ordinal]['elapsed_seconds'] for ordinal in order]
    for kind in ('exact', 'improvement', 'fault_gradient'):
        positions = [i + 1 for i, ordinal in enumerate(order)
                     if _success(by_ordinal[ordinal], baseline, kind)]
        result[kind] = {'top4': bool(positions and positions[0] <= 4),
                        'top8': bool(positions and positions[0] <= 8),
                        'top4_replay_calls': min(4, len(order)),
                        'top8_replay_calls': min(8, len(order)),
                        'top4_replay_compile_seconds': sum(elapsed[:4]),
                        'top8_replay_compile_seconds': sum(elapsed[:8]),
                        'first_call': positions[0] if positions else None,
                        'first_success_replay_compile_seconds': (
                            sum(elapsed[:positions[0]]) if positions else None),
                        'pool_has_success': bool(positions)}
    return result


def state_prediction_error(parent: dict, children: list[dict], ranked: list) -> dict:
    """The model's lossy state distance against the unchanged-parent baseline."""
    by_ordinal = {c['ordinal']: c for c in children}
    predicted_sum = unchanged_sum = predicted_only_sum = predicted_only_baseline = 0.
    compared = abstained = predicted_count = 0
    effects = effect_module()
    for proposal in ranked:
        child = by_ordinal[proposal['ordinal']]
        after = child.get('after')
        forecast = proposal.get('forecast') or {}
        predicted = forecast.get('predicted_state')
        if not after or not isinstance(predicted, dict):
            abstained += 1
            continue
        predicted_sum += effects.state_distance(predicted, after)
        unchanged_sum += effects.state_distance(parent, after)
        compared += 1
        if forecast.get('status') == 'predicted':
            predicted_count += 1
            predicted_only_sum += effects.state_distance(predicted, after)
            predicted_only_baseline += effects.state_distance(parent, after)
    return {'compared': compared, 'abstained_or_unavailable': abstained,
            'predicted_count': predicted_count,
            'predicted_mean_distance': predicted_sum / compared if compared else None,
            'no_change_mean_distance': unchanged_sum / compared if compared else None,
            'predicted_only_mean_distance': (
                predicted_only_sum / predicted_count if predicted_count else None),
            'predicted_only_no_change_mean_distance': (
                predicted_only_baseline / predicted_count if predicted_count else None)}


def report(args: argparse.Namespace, manifest: dict) -> None:
    rank_record = read_sealed(args.run / 'evaluation-rankings.json')
    model_record = read_sealed(args.run / 'model.json')
    rank_by_name = {r['function']: r for r in rank_record['rankings']}
    rows = []
    for root in (r for r in manifest['roots'] if r['split'] == 'evaluation'):
        base, baseline_path = root_paths(args.run, root)
        parent = json.loads(baseline_path.read_text())
        children = json.loads((base / 'children.json').read_text())['children']
        rank = rank_by_name[root['function']]
        if 'ordinals' not in rank:
            rows.append({'function': root['function'], 'excluded_reason': rank['excluded_reason'],
                         'pool_compiles': len(children) + 1,
                         'baseline_seconds': parent['baseline']['elapsed_seconds'],
                         'children_seconds': sum(c['elapsed_seconds'] for c in children)})
            continue
        ordinary = [p['ordinal'] for p in root['proposals']]
        predicted = rank['ordinals']
        rows.append({'function': root['function'], 'group': root['tu'],
                     'proposal_count': len(children), 'pool_compiles': len(children) + 1,
                     'original': replay(children, parent['baseline'], ordinary),
                     'predicted': replay(children, parent['baseline'], predicted),
                     'state_prediction': state_prediction_error(parent['before'], children,
                                                                rank['ranked']),
                     'forecast_coverage': [{
                         'ordinal': p['ordinal'], 'family': next(x['family'] for x in root['proposals']
                                                                if x['ordinal'] == p['ordinal']),
                         'status': p['forecast']['status'],
                         'support_groups': p['forecast']['support_groups']}
                         for p in rank['ranked']],
                     'feature_seconds': rank['feature_seconds'],
                     'rank_seconds': rank['rank_seconds'],
                     'baseline_seconds': parent['baseline']['elapsed_seconds'],
                     'children_seconds': sum(c['elapsed_seconds'] for c in children)})
    included = [r for r in rows if 'predicted' in r]
    development = results_for(args.run, [r for r in manifest['roots']
                                         if r['split'] == 'development'])
    dev_compiles = sum(1 + len(r['children']) for r in development)
    dev_seconds = sum(r['baseline']['elapsed_seconds'] +
                      sum(c['elapsed_seconds'] for c in r['children']) for r in development)
    evaluation_compiles = sum(r['pool_compiles'] for r in rows)
    evaluation_seconds = sum(r.get('baseline_seconds', 0) + r.get('children_seconds', 0)
                             for r in rows)
    summary = {'kind': 'prospective-compiler-effects-report-v1',
               'manifest_sha256': digest((args.run / 'manifest.json').read_bytes()),
               'model_sha256': rank_record['model_sha256'],
               'ranking_sha256': digest((args.run / 'evaluation-rankings.json').read_bytes()),
               'cohort': manifest['split_counts'], 'evaluated': len(included),
               'excluded_baselines': len(rows) - len(included),
               'actual_pool_compiles': dev_compiles + evaluation_compiles,
               'development_pool_compiles': dev_compiles,
               'evaluation_pool_compiles': evaluation_compiles,
               'development_pool_seconds': dev_seconds,
               'evaluation_pool_seconds': evaluation_seconds,
               'fit_seconds': model_record['fit_seconds'],
               'top4_replay_calls': sum(min(4, r['proposal_count']) for r in included),
               'top8_replay_calls': sum(min(8, r['proposal_count']) for r in included),
               'state_vector_limit': 'Lossy counts retain architectural register operands but omit their instruction positions, value linkage and immediates.',
               'replay_time_limit': 'Replay times sum measured full-pool compiles; they are not online wall time.',
               'inference_seconds': sum(r.get('feature_seconds', 0) + r.get('rank_seconds', 0)
                                        for r in rows), 'functions': rows,
               'aggregate': {}}
    for kind in ('exact', 'improvement', 'fault_gradient'):
        summary['aggregate'][kind] = {
            policy: {cut: sum(r[policy][kind][cut] for r in included)
                     for cut in ('top4', 'top8')}
            for policy in ('original', 'predicted')}
        summary['aggregate'][kind]['available_successes'] = sum(
            r['original'][kind]['pool_has_success'] for r in included)
        summary['aggregate'][kind]['first_call_when_success_exists'] = {
            policy: [r[policy][kind]['first_call'] for r in included
                     if r[policy][kind]['pool_has_success']]
            for policy in ('original', 'predicted')}
        summary['aggregate'][kind]['replay_compile_seconds'] = {
            policy: {
                'top4': sum(r[policy][kind]['top4_replay_compile_seconds'] for r in included),
                'top8': sum(r[policy][kind]['top8_replay_compile_seconds'] for r in included),
                'first_success_where_exists': sum(
                    r[policy][kind]['first_success_replay_compile_seconds'] for r in included
                    if r[policy][kind]['pool_has_success'])}
            for policy in ('original', 'predicted')}
        summary['aggregate'][kind]['replay_prediction_overhead_seconds'] = {
            'original': 0.,
            'predicted': sum(r['feature_seconds'] + r['rank_seconds'] for r in included)}
    compared = sum(r['state_prediction']['compared'] for r in included)
    predicted_count = sum(r['state_prediction']['predicted_count'] for r in included)
    summary['state_prediction'] = {
        'compared': compared,
        'predicted_count': predicted_count,
        'abstained_or_unavailable': sum(r['state_prediction']['abstained_or_unavailable']
                                       for r in included),
        'predicted_mean_distance': (
            sum(r['state_prediction']['predicted_mean_distance'] * r['state_prediction']['compared']
                for r in included if r['state_prediction']['compared']) / compared
            if compared else None),
        'no_change_mean_distance': (
            sum(r['state_prediction']['no_change_mean_distance'] * r['state_prediction']['compared']
                for r in included if r['state_prediction']['compared']) / compared
            if compared else None),
        'predicted_only_mean_distance': (
            sum(r['state_prediction']['predicted_only_mean_distance'] *
                r['state_prediction']['predicted_count'] for r in included
                if r['state_prediction']['predicted_count']) / predicted_count
            if predicted_count else None),
        'predicted_only_no_change_mean_distance': (
            sum(r['state_prediction']['predicted_only_no_change_mean_distance'] *
                r['state_prediction']['predicted_count'] for r in included
                if r['state_prediction']['predicted_count']) / predicted_count
            if predicted_count else None)}
    coverage = Counter((p['family'], p['status']) for r in included for p in r['forecast_coverage'])
    summary['forecast_coverage'] = {
        'by_family_status': {f'{family}:{status}': count for (family, status), count in sorted(coverage.items())},
        'support_groups': dict(sorted(Counter(str(p['support_groups']) for r in included
                                               for p in r['forecast_coverage']).items()))}
    seal(args.run / 'report.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'functions'},
                     indent=2, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('freeze', 'pin-code', 'run-dev', 'fit', 'prepare-eval',
                                          'run-eval', 'report'))
    parser.add_argument('--run', type=Path,
                        default=Path('/home/grant/decomp/experiments/compiler-effects-20260926'))
    parser.add_argument('--campaign', type=Path,
                        default=Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.json'))
    parser.add_argument('--native-db', type=Path,
                        default=Path('/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite'))
    parser.add_argument('--kb', type=Path, default=Path('/home/grant/decomp/kb-sbk1.sqlite'))
    parser.add_argument('--repo', type=Path, default=Path('/home/grant/decomp/sbk1'))
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    if args.stage == 'freeze':
        freeze(args)
        return
    manifest = load_manifest(args.run)
    if args.stage == 'pin-code':
        pin_code(args.run, manifest)
        return
    check_code_pins(args.run, manifest)
    if args.stage == 'run-dev':
        run_split(args, manifest, 'development', True)
    elif args.stage == 'fit':
        fit_model(args, manifest)
    elif args.stage == 'prepare-eval':
        prepare_evaluation(args, manifest)
    elif args.stage == 'run-eval':
        if args.workers < 1 or args.workers > MAX_WORKERS:
            raise ValueError('workers must be 1..3')
        score_evaluation_children(args, manifest)
    elif args.stage == 'report':
        report(args, manifest)


if __name__ == '__main__':
    main()
