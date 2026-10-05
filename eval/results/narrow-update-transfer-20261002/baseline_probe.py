"""Two-call viability probe for the independently drafted applicable root."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent


def main():
    census = json.loads((HERE / 'census.json').read_text())
    work = Path(census['work'])
    protocol = json.loads((work / 'protocol.json').read_text())
    for name, digest in protocol['code_pins'].items():
        assert hashlib.sha256((work / 'code' / name).read_bytes()).hexdigest() == digest
    sys.path.insert(0, str(work / 'code'))
    from solver import workspace, narrow_update
    roots = [r for r in json.loads((HERE / 'lowered-fallback.json').read_text())['rows'] if r.get('direct_proposals')]
    assert len(roots) == 1
    root = roots[0]
    name = root['function']
    source = Path(root['source']).read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == root['source_sha256']
    folder = work / 'baseline-probe'
    folder.mkdir(exist_ok=False)
    plan = {'function': name, 'root_sha256': root['source_sha256'], 'max_candidate_evaluations': 2,
            'question': 'Does the source-independent lowered root compile, and can its first guarded proposal be certified?',
            'scope': 'Viability probe, not the equal-budget transfer comparison; no ABI placeholder guesses or game-header drafting.',
            'training_eligible': False, 'model_calls': 0}
    (folder / 'protocol.json').write_text(json.dumps(plan, indent=2) + '\n')
    original = Path('/home/grant/decomp/sbk2')
    repo = folder / 'repo'
    repo.mkdir()
    for path in original.iterdir():
        if path.name in {'.git', 'tools', 'nonmatchings'}:
            continue
        (repo / path.name).symlink_to(path, target_is_directory=path.is_dir())
    tools = repo / 'tools'
    tools.mkdir()
    for path in (original / 'tools').iterdir():
        dest = tools / path.name
        if path.is_dir():
            dest.symlink_to(path, target_is_directory=True)
        else:
            shutil.copy2(path, dest)
    ws = workspace.bootstrap(repo, name)
    conn = sqlite3.connect(folder / 'trial.sqlite')
    conn.executescript((work / 'code/kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS origin', ('file:/home/grant/decomp/kb-sbk2.sqlite?mode=ro',))
    for table in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM origin.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE origin')
    candidates = [('baseline', source), next(narrow_update.variants(source, name))]
    rows = []
    parent = None
    for index, (label, candidate) in enumerate(candidates):
        started = time.monotonic()
        attempt = workspace.score(ws, repo, 'transfer_probe_' + str(index), candidate, conn=conn, func=name,
            strategy='narrow-transfer:viability:' + label, run_id='narrow-transfer-20261002',
            parent_attempt_id=parent, relation='candidate-construction',
            extra={'training_eligible': False, 'assistance': 'assembly-only construction; ordinary project oracle context',
                   'root_source_sha256': root['source_sha256'], 'scope': 'SBK2 KMC GCC viability; not clean IDO transfer'})
        conn.commit()
        parent = attempt.receipt_id
        row = {'label': label, 'attempt_id': parent, 'compiled': attempt.compiled,
               'frontend_passed': (attempt.frontend or {}).get('passed'),
               'exact': workspace.repair_complete(attempt), 'seconds': time.monotonic() - started,
               'diagnostics': attempt.diff[:2400], 'source_sha256': hashlib.sha256(candidate.encode()).hexdigest()}
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != 'diagnostics'}), flush=True)
    result = {'status': 'native_viability_probe_complete', 'function': name, 'rows': rows,
              'compiler_evaluations': len(rows), 'training_eligible': False,
              'comparison_run': False, 'work': str(folder)}
    (HERE / 'baseline-probe.json').write_text(json.dumps(result, indent=2) + '\n')
    conn.close()


if __name__ == '__main__':
    main()
