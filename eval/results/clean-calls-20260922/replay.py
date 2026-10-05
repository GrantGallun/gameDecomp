"""Frozen 200-candidate compiler replay; all compiles are logged in a private DB."""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_workers
from eval.intake_probe import SEQUENCE
from eval.intake_runners import RUNNERS
from eval.intake_search import search, _preserves, _quality
from eval.intake_blockers import inventory
from eval.tool_agent_run import _attempt_to_verdict
from solver import frontend_diagnostics, workspace, rewrites

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent / 'clean-composition-20260922'
REPO = Path.home() / 'decomp/sbk1'
NATIVE = Path.home() / 'decomp/experiments/clean-calls-20260922'


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def digest(source):
    return hashlib.sha256(source.encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', choices=('baseline', 'search', 'objects'), required=True)
    ap.add_argument('--limit', type=int, default=200)
    ap.add_argument('--tag', default='reviewed')
    ap.add_argument('--baseline-tag', default='reviewed')
    args = ap.parse_args()
    NATIVE.mkdir(parents=True, exist_ok=True)
    ro = sqlite3.connect(f"file:{Path.home() / 'decomp/kb-sbk1.sqlite'}?mode=ro", uri=True)
    conn = sqlite3.connect(NATIVE / 'attempts.sqlite')
    conn.executescript((ROOT / 'kb/schema.sql').read_text())
    for table in ('tus', 'functions'):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
        conn.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                         ro.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall())
    conn.commit()
    evidence_path = NATIVE / 'binary-evidence.sqlite'
    if not evidence_path.exists():
        with sqlite3.connect(evidence_path) as frozen:
            frozen.executescript((ROOT / 'kb/schema.sql').read_text())
            ro.execute('BEGIN')
            for table in ('extraction', 'tus', 'functions', 'evidence'):
                cols = [r[1] for r in frozen.execute(f'PRAGMA table_info({table})')]
                frozen.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                                   ro.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall())
            ro.rollback()
    ro.close()
    evidence = sqlite3.connect(f'file:{evidence_path}?mode=ro', uri=True)
    evidence_receipt = dict(path=str(evidence_path), sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                            rows=evidence.execute('SELECT count(*) FROM evidence').fetchone()[0])
    assert evidence_receipt['rows'] > 0
    prior = json.loads((PRIOR / 'final.json').read_text())
    targets = {r['function']: r['target'] for r in json.loads((OUT.parent / 'clean-residuals-20260922' / 'census.json').read_text())['rows']}
    code = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for directory in ('solver', 'eval', 'kb', 'oracle') for p in (ROOT / directory).glob('*.py')}
    filename = {'baseline': 'baseline', 'search': 'paired', 'objects': 'objects'}[args.phase] + f'-{args.tag}.json'
    path = OUT / filename
    existing = json.loads(path.read_text()) if path.exists() else {'rows': [], 'code_sha256': code}
    assert existing['code_sha256'] == code, 'code changed during a resumable arm; use a fresh receipt'
    rows = existing['rows']
    done = {r['function'] for r in rows}
    baseline = {} if args.phase == 'baseline' else {
        r['function']: r for r in json.loads((OUT / f'baseline-{args.baseline_tag}.json').read_text())['rows']}
    entries = prior['rows']
    if args.phase == 'objects':
        entries = sorted([r for r in entries if baseline[r['function']]['verdict']['compiled']
            and baseline[r['function']]['frontend']['status'] == 'passed'
            and not baseline[r['function']]['verdict']['exact']
            and baseline[r['function']]['verdict']['score'] >= 95],
            key=lambda r: baseline[r['function']]['verdict']['score'], reverse=True)
    started = time.monotonic()
    for entry in entries:
        name = entry['function']
        if name in done:
            continue
        if len(rows) >= args.limit:
            break
        folder = OUT / f'states-{args.tag}' / name
        folder.mkdir(parents=True, exist_ok=True)
        source = entry['source']
        assert digest(source) == entry['source_sha256'], name
        native = campaign_workers.isolate(REPO, NATIVE / f'{args.phase}-{args.tag}-builds' / name, name)
        ws = native / 'nonmatchings' / name
        ids = {sha: ident for ident, sha in conn.execute(
            'SELECT a.id,a.source_sha256 FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=?', (name,))}
        def score(candidate, parent, action):
            att = workspace.score(ws, native, name, candidate, conn=conn, func=name,
                strategy=f'clean-calls:{args.phase}:{action}', model='zero-model',
                run_id=f'clean-calls-20260922:{args.tag}:{args.phase}', parent_attempt_id=ids.get(parent),
                relation='repair', action=action, extra={'parent_source_sha256': parent})
            assert att.receipt_id is not None
            ids[digest(candidate)] = att.receipt_id
            return _attempt_to_verdict(att)
        def observe(candidate):
            return frontend_diagnostics.analyse(candidate, repo=native, target=targets[name], full_diagnostics=True)
        if args.phase == 'baseline':
            verdict, front = score(source, None, 'baseline'), observe(source)
            assert (verdict['compiled'], verdict['exact'], front['status']) == (
                entry['verdict']['compiled'], entry['verdict']['exact'], entry['frontend']['status']), name
            row = dict(function=name, source=source, source_sha256=digest(source), target=targets[name],
                       verdict=verdict, frontend=front, trace=[], previous_error_count=entry['frontend']['error_count'])
            (folder / 'before.c').write_text(source)
            save(folder / 'before-frontend.json', front)
        elif args.phase == 'search':
            initial = baseline[name]
            context = dict(candidate=source, function=name, repo=str(native), target=targets[name],
                workspace=str(ws), target_asm_path=str(ws / 'target.s'), kb_conn=evidence,
                initial_verdict=initial['verdict'])
            result = search(context, runners=RUNNERS, sequence=SEQUENCE, score=score, observe=observe,
                            max_rounds=3, max_attempts=12, beam_width=3)
            assert result['nodes'][0]['frontend']['error_count'] == initial['frontend']['error_count']
            row = dict(function=name, before=initial, **result)
            (folder / 'after.c').write_text(result['source'])
            save(folder / 'after-frontend.json', result['frontend'])
            save(folder / 'search.json', result)
        else:
            initial = baseline[name]
            best = dict(source=source, verdict=initial['verdict'], frontend=initial['frontend'])
            trace, seen = [], {digest(source)}
            for round_index in range(3):
                parent = best
                proposals = rewrites.propose(parent['source'], parent['verdict']['diff'])
                if not proposals:
                    trace.append(dict(changed=False, action='solver.rewrites.propose', reason='no applicable generator'))
                    break
                progressed = False
                for rewrite in proposals:
                    candidate = rewrite(parent['source'])
                    sha = digest(candidate)
                    if sha in seen:
                        continue
                    if sum(t.get('changed', False) for t in trace) >= 12:
                        break
                    seen.add(sha)
                    v, f = score(candidate, digest(parent['source']), rewrite.label), observe(candidate)
                    node = dict(source=candidate, verdict=v, frontend=f)
                    adopted = _preserves(node, best) and _quality(node) > _quality(best)
                    trace.append(dict(action=rewrite.label, kind=rewrite.kind, changed=True, adopted=adopted,
                        parent=digest(parent['source']), source_sha256=sha, source=candidate, verdict=v, frontend=f))
                    if adopted:
                        best, progressed = node, True
                    if best['verdict']['exact']:
                        break
                if not progressed or best['verdict']['exact']:
                    break
            row = dict(function=name, before=initial, **best, trace=trace,
                       attempts=sum(t.get('changed', False) for t in trace), source_sha256=digest(best['source']))
            (folder / 'object-after.c').write_text(best['source'])
        rows.append(row)
        receipt = dict(rows=rows, expected=len(entries), code_sha256=code,
            evidence=evidence_receipt,
            attempt_db=str(NATIVE / 'attempts.sqlite'), calls=conn.execute('SELECT count(*) FROM attempts').fetchone()[0],
            seconds=existing.get('seconds', 0) + time.monotonic() - started)
        save(path, receipt)
        print(json.dumps(dict(phase=args.phase, n=len(rows), function=name,
            compiled=row['verdict']['compiled'], exact=row['verdict']['exact'], errors=row['frontend']['error_count'],
            attempts=row.get('attempts', 1), stop=row.get('stop_reason'))), flush=True)
    if args.phase == 'baseline' and len(rows) == 200:
        save(OUT / f'inventory-before-{args.tag}.json', inventory(rows))
    conn.close()
    evidence.close()


if __name__ == '__main__':
    main()
