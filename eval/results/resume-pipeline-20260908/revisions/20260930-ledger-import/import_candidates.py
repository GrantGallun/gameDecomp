"""Import re-verified candidates into the live campaign, keeping their provenance. Owner: "whatever you think is
correct long term" (2026-09-30), after the proposal to import the kb-ledger candidates with their tier kept.

Why: 32 of the 36 candidates the jump-table certificate makes function-exact, and today's 14 search exacts, live only
in the kb ledger or in trial databases. The campaign's nodes hold other, worse sources, so no campaign route
re-verifies them, and none can reach the whole-ROM integration sweep.

Rules, after eval/cohort_reconcile.py ("re-verify, do not believe"):
- Every candidate is compiled again through the ordinary path (workspace.score) against the campaign ledger. Only a
  result that is object-exact, or function-exact under the ROM-backed certificate, is applied.
- The applied attempt's strategy begins with the ROOT source's own strategy, so eval.status keeps the tier:
  `ledger-import:authorized-target-history-recovery|via:...` stays in the recovered tier. That also closes, for these
  rows, the lineage gap eval/status.py documents: edits on recovered sources used to count as SOLVED.
- The node changes only through the controller's own `completion_campaign.accept`, which is how every job result is
  applied: object_exact, function_exact_pending_integration, or pending if the frontend refuses.
- Skipped: nodes already done, the sealed held-out 50, candidates equal to the node's source, and names outside the
  campaign cohort (its excluded held-out sets).

    python import_candidates.py --dry      # score into a trial copy; state untouched; writes import-dry.json
    python import_candidates.py --apply    # both pause markers set, nothing in flight; campaign ledger and state
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTROL = HERE.parents[1]
FROZEN = CONTROL / 'code'
MAIN = HERE.parents[4]
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
STATE = NATIVE / 'campaign.json'
REPO = Path('/home/grant/decomp/sbk1')
RESULTS = MAIN / 'eval' / 'results' / 'loop-shape-20260930'
LEDGERS = {'campaign': NATIVE / 'campaign.sqlite', 'kb': Path('/home/grant/decomp/kb-sbk1.sqlite')}
TRIALS = {'site-edit': [Path('/home/grant/decomp/runs/loop-shape-20260930/near.sqlite'),
                        Path('/home/grant/decomp/runs/loop-shape-20260930/band1030.sqlite')],
          'plateau': [Path('/home/grant/decomp/runs/loop-shape-20260930/plateau.sqlite')]}
DRY_DB = Path('/home/grant/decomp/runs/ledger-import-20260930/dry.sqlite')
DONE = {'object_exact', 'integrated', 'function_exact_pending_integration'}
PROFILE = 'ledger-import@20260930'


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _row(db: Path, attempt_id: int):
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        return conn.execute('SELECT f.name, a.source_code, a.strategy FROM attempts a JOIN functions f '
                            'ON f.addr=a.func_addr WHERE a.id=?', (attempt_id,)).fetchone()
    finally:
        conn.close()


def candidates() -> list[dict]:
    """(function, source, root strategy, origin) for every certificate and search result of 2026-09-30."""
    out = {}
    provenance = json.loads((RESULTS / 'provenance.json').read_text())
    for line in (RESULTS / 'rescore_fb.jsonl').read_text().splitlines():
        r = json.loads(line)
        if not (r.get('function_exact') or r.get('exact')):
            continue
        name, source, strategy = _row(Path(r['ledger']), r['attempt_id'])
        ledger = 'kb' if 'kb-sbk1' in r['ledger'] else 'campaign'
        out[name] = {'function': name, 'source': source, 'root_strategy': strategy or '',
                     'origin': f"{ledger}#{r['attempt_id']}", 'route': 'rescore-certificate'}
    for route, dbs in TRIALS.items():
        for db in dbs:
            if not db.exists():
                continue
            conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            rows = conn.execute('SELECT f.name, a.source_code, a.id FROM attempts a JOIN functions f '
                                'ON f.addr=a.func_addr WHERE a.exact=1 ORDER BY a.id').fetchall()
            conn.close()
            for name, source, aid in rows:
                if name in out or name not in provenance:
                    continue
                out[name] = {'function': name, 'source': source,
                             'root_strategy': provenance[name]['start'], 'origin': f'{db.stem}#{aid}',
                             'route': route}
    return sorted(out.values(), key=lambda c: c['function'])


def strategy(c: dict) -> str:
    return f"ledger-import:{c['root_strategy']}|via:{c['route']}:{c['origin']}"[:120]


def _init_dry():
    DRY_DB.parent.mkdir(parents=True, exist_ok=True)
    if DRY_DB.exists():
        return
    conn = sqlite3.connect(DRY_DB)
    conn.executescript((MAIN / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS c', (f"file:{LEDGERS['campaign']}?mode=ro",))
    for table in ('extraction', 'tus', 'functions'):
        conn.execute(f'INSERT INTO main.{table} SELECT * FROM c.{table}')
    conn.commit()
    conn.execute('DETACH DATABASE c')
    conn.close()


def _lock(path: Path):
    handle = path.open('a+b')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError(f'lock held: {path}') from exc
    return handle


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry', action='store_true')
    mode.add_argument('--apply', action='store_true')
    ap.add_argument('--only', nargs='*')
    args = ap.parse_args()
    sys.path.insert(0, str(FROZEN))
    from eval import campaign_state, completion_campaign  # noqa: E402  frozen controller code
    from solver import residual, site_edits, workspace   # noqa: E402
    sealed = {x['name'] for x in json.loads((MAIN / 'eval/results/heldout50-20260929/frame.json').read_text())}
    handles = []
    if args.apply:
        if not (CONTROL / 'service.pause').exists() or not (NATIVE / 'service.pause').exists():
            raise RuntimeError('both campaign pause markers must exist before importing')
        handles = [_lock(CONTROL / 'resume-supervisor.lock'), _lock(STATE.with_suffix('.lock'))]
    try:
        state = campaign_state.read(STATE)
        if args.apply and (state.get('fast_inflight') or state.get('inflight')):
            raise RuntimeError('campaign has work in flight')
        if args.dry:
            _init_dry()
        db = LEDGERS['campaign'] if args.apply else DRY_DB
        conn = sqlite3.connect(db, timeout=300)
        artifacts = NATIVE / 'campaign-artifacts' if args.apply else DRY_DB.parent / 'artifacts'
        artifacts.mkdir(parents=True, exist_ok=True)
        report, changed = [], []
        todo = candidates()
        if args.only:
            todo = [c for c in todo if c['function'] in set(args.only)]
        for c in todo:
            name = c['function']
            row = {'function': name, 'origin': c['origin'], 'route': c['route'], 'strategy': strategy(c),
                   'recovered_tier': any(k in c['root_strategy'] for k in
                                         ('history-recovery', 'historical-provenance', 'symbol-restoration'))}
            node = state['nodes'].get(name)
            if node is None:
                row['skipped'] = 'not in campaign cohort'
            elif name in sealed:
                row['skipped'] = 'sealed held-out 50'
            elif node['status'] in DONE:
                row['skipped'] = 'node already ' + node['status']
            elif node.get('source_sha256') == _h(c['source']):
                row['skipped'] = 'already the node source'
            if 'skipped' in row:
                report.append(row)
                continue
            ws = workspace.bootstrap(REPO, name)
            tag = f'{name}_ledgerimport_{time.time_ns()}'
            attempt = workspace.score(ws, REPO, tag, c['source'], conn=conn, func=name, strategy=row['strategy'],
                                      run_id=f'ledger-import-20260930-{name}', run_kind='ledger-import',
                                      parent_attempt_id=node.get('attempt_id'), relation='ledger-import',
                                      action=c['origin'][:120])
            conn.commit()
            boundary = (attempt.verification or {}).get('function_boundary') or {}
            complete = workspace.repair_complete(attempt)
            row.update(compiled=bool(attempt.compiled), exact=complete,
                       function_exact=boundary.get('function_exact') is True, attempt_id=attempt.receipt_id)
            if not (complete or row['function_exact']):
                row['skipped'] = 'did not re-verify as exact or function-exact'
                report.append(row)
                continue
            path = artifacts / f'{tag}.c'
            path.write_text(c['source'])
            packet = residual.build(attempt, target_asm=workspace.target_asm(ws, name),
                                    target_object=ws / 'target.o', candidate_object=ws / f'{tag}.o').to_dict()
            result = {'status': 'evaluated', 'source': str(path), 'source_sha256': _h(c['source']),
                      'attempt_id': attempt.receipt_id, 'score': attempt.score, 'residual': packet,
                      'verification': attempt.verification, 'exact': complete, 'semantic_validation': None}
            receipt = artifacts / f'{tag}.import.json'
            receipt.write_text(json.dumps({**row, 'rule': __doc__.splitlines()[0]}, indent=1))
            if args.apply:
                completion_campaign.accept(node, {'name': PROFILE, 'model': False}, result, receipt)
                row['status_after'] = node['status']
                changed.append(name)
            else:
                trial = json.loads(json.dumps(node))
                completion_campaign.accept(trial, {'name': PROFILE, 'model': False}, result, receipt)
                row['status_after'] = trial['status']
            report.append(row)
            print(name, row.get('status_after'), row['strategy'][:70], flush=True)
        conn.close()
        if args.apply and changed:
            campaign_state.Store(STATE).save(state, changed=changed)
        out = HERE / ('import-applied.json' if args.apply else 'import-dry.json')
        out.write_text(json.dumps({'mode': 'apply' if args.apply else 'dry', 'changed': changed,
                                   'rows': report}, indent=1))
        from collections import Counter
        print(json.dumps(Counter(r.get('status_after') or r.get('skipped') for r in report), indent=1))
    finally:
        for h in reversed(handles):
            h.close()


if __name__ == '__main__':
    main()
