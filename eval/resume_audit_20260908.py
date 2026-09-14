"""Recompile a fixed saved-candidate census; never generate reference bodies."""
import concurrent.futures
import hashlib
import json
import sqlite3
import subprocess
import time
import tempfile
from collections import Counter
from pathlib import Path

from solver import workspace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'eval/results/resume-20260908'
REPO = Path.home() / 'decomp/sbk1'


def main():
    OUT.mkdir(exist_ok=False)
    start = time.time()
    original = sqlite3.connect(f'file:{Path.home()}/decomp/kb-sbk1.sqlite?mode=ro', uri=True)
    scratch = tempfile.TemporaryDirectory(prefix='resume-census-')
    snapshot = Path(scratch.name) / 'receipts.sqlite'
    conn = sqlite3.connect(snapshot)
    original.backup(conn)
    original.close()
    conn.row_factory = sqlite3.Row
    functions = [dict(r) for r in conn.execute('select f.*, t.name as tu from functions f left join tus t on t.id=f.tu_id order by f.addr')]
    selected = {}
    # Freeze selection before any new scores exist. Historical exact first, then
    # compiling/highest similarity, then newest as a deterministic tie-breaker.
    for r in conn.execute('select id,func_addr,source_code,strategy,score,exact,compiled,parent_attempt_id from attempts where source_code is not null and length(source_code)>0 order by coalesce(exact,0) desc, compiled desc, score desc, id desc'):
        selected.setdefault(r['func_addr'], dict(r))
    strategies = {}
    for r in conn.execute('select distinct func_addr,strategy from attempts where exact=1'):
        strategies.setdefault(r['func_addr'], []).append(r['strategy'] or '')
    inventory = {'function_records': len(functions), 'function_bytes': sum(f['size'] or 0 for f in functions),
                 'attempts': conn.execute('select count(*) from attempts').fetchone()[0],
                 'evidence_rows': conn.execute('select count(*) from evidence').fetchone()[0],
                 'selected_candidates': len(selected), 'selection': 'one saved candidate per function: historical exact, compiled, score, newest id',
                 'scope': 'development corpus, existing candidates, project headers/compiler; not fresh autonomous benchmark or whole-ROM verification',
                 'git_head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                 'started_at_unix': start}
    (OUT / 'inventory.json').write_text(json.dumps(inventory, indent=2))
    print(json.dumps(inventory), flush=True)

    def check(f):
        row = {'function': f['name'], 'address': f['addr'], 'size': f['size'], 'tu': f['tu']}
        candidate = selected.get(f['addr'])
        if not candidate:
            return {**row, 'status': 'unattempted', 'exact': False}
        tags = strategies.get(f['addr'], []) + [candidate['strategy'] or '']
        provenance = ('reference_recovered' if any(any(x in s for x in ('history-recovery','historical-provenance','symbol-restoration')) for s in tags)
                      else 'header_assisted_tagged' if any('project-header' in s for s in tags)
                      else 'other_development_candidate')
        row.update(attempt_id=candidate['id'], strategy=candidate['strategy'], provenance=provenance,
                   historical_exact=candidate['exact'], historical_score=candidate['score'],
                   source_sha256=hashlib.sha256(candidate['source_code'].encode()).hexdigest())
        # Private per-function receipt DB provides authoritative TU metadata and
        # logs this verification without changing the production knowledge base.
        db = sqlite3.connect(snapshot, timeout=120)
        try:
            ws = workspace.bootstrap(REPO, f['name'])
            att = workspace.score(ws, REPO, 'resume_20260908', candidate['source_code'], conn=db,
                                  func=f['name'], strategy='resume-census-reverification',
                                  extra={'original_attempt_id': candidate['id']})
            row.update(status='compiled' if att.compiled else 'compile_failed', compiled=att.compiled,
                       exact=att.exact, similarity=att.score, frontend=att.frontend,
                       verification=att.verification, error=att.compiler_stderr)
        except Exception as exc:
            row.update(status='error', exact=False, error=f'{type(exc).__name__}: {exc}')
        finally:
            db.close()
        (OUT / (f['name'] + '.json')).write_text(json.dumps(row, indent=2))
        return row

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for row in pool.map(check, functions):
            rows.append(row)
            if row['status'] != 'unattempted':
                print(f"{len(rows)}/{len(functions)} {row['function']} {row['status']} exact={row['exact']}", flush=True)
    summary = {**inventory, 'elapsed_seconds': time.time()-start, 'status': 'complete',
               'outcomes': dict(Counter(r['status'] for r in rows)),
               'exact_functions': sum(r['exact'] for r in rows),
               'exact_function_bytes': sum(r['size'] or 0 for r in rows if r['exact']),
               'exact_by_provenance': dict(Counter(r['provenance'] for r in rows if r['exact'])),
               'rows': rows}
    (OUT / 'report.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({k:v for k,v in summary.items() if k != 'rows'}, indent=2), flush=True)
    conn.close()
    scratch.cleanup()


if __name__ == '__main__':
    main()
