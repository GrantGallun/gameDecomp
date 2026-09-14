"""Isolated real-MIPS duplicate compilation replay; no campaign writes/model calls."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

PROJECT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(PROJECT))
from eval import campaign_workers, completion_campaign, fast_runtime, frozen_wavefront
from solver import fresh_compile, refine, workspace


def main():
    report_dir = PROJECT/'eval/results/optimization-audit-20260911'
    root = Path('/home/grant/decomp/fresh-compile-replay-20260911') / str(time.time_ns())
    original = Path('/home/grant/decomp/sbk1')
    function = 'updateMenuSpriteActorDebugControls'
    source_path = PROJECT/'eval/results/resume-pipeline-20260908/campaign-artifacts/1789083417724465675-updateMenuSpriteActorDebugControls.best.c'
    source = source_path.read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == '68e493e393f420eb4316c9971de795d3d97571a09e1348ecbed2f3ef4745f3ab'
    repo = campaign_workers.isolate(original, root/'repo', function)
    ws = repo/'nonmatchings'/function
    target = json.loads((ws/'.compiler-target.json').read_text())['target']
    conn = sqlite3.connect(root/'attempts.sqlite')
    refine.ensure_schema(conn)
    conn.execute('INSERT INTO tus(id,name) VALUES(1,?)', (target,))
    conn.execute('INSERT INTO functions(addr,name,tu_id,state) VALUES(1,?,1,?)', (function, 'attempted'))
    conn.commit()
    pins = completion_campaign._pins(PROJECT, repo)
    pin = completion_campaign.digest(pins)
    metrics = fast_runtime.install(root/'cache', pin, root/'model.lock')
    assert workspace._verified_build_cache_pin == pin
    results = []
    calls = dict(frontend=0, certificate=0)
    from solver import frontend_check, byte_certificate
    original_frontend, original_certificate = frontend_check.check, byte_certificate.certify
    def frontend(*args, **kwargs):
        calls['frontend'] += 1
        return original_frontend(*args, **kwargs)
    def certificate(*args, **kwargs):
        calls['certificate'] += 1
        return original_certificate(*args, **kwargs)
    frontend_check.check, byte_certificate.certify = frontend, certificate
    for arm in ('distinct_names', 'same_job_names'):
        ledger = fresh_compile.Names(repo, ws)
        start = time.perf_counter()
        previous = None
        before = dict(metrics)
        attempts = []
        for number in range(3):
            requested = function+'_'+arm+'_'+str(number)
            kwargs = dict(conn=conn, func=function, strategy='fresh-compile-replay',
                run_id=arm, parent_attempt_id=previous, relation='reverify', action='unchanged saved candidate')
            if arm == 'same_job_names':
                name, attempt = ledger.score(requested, source, **kwargs)
            else:
                name, attempt = requested, workspace.score(ws, repo, requested, source, **kwargs)
            assert attempt.exact and attempt.frontend['passed'], asdict(attempt)
            previous = attempt.receipt_id
            attempts.append({'name':name, 'attempt_id':attempt.receipt_id,
                'object_sha256':hashlib.sha256((ws/(name+'.o')).read_bytes()).hexdigest(),
                'frontend_passed':attempt.frontend['passed'], 'exact':attempt.exact,
                'score':attempt.score, 'attribution_status':attempt.source_attribution.get('status')})
        results.append({'arm':arm, 'seconds':time.perf_counter()-start, 'attempts':attempts,
            'compile_hits':metrics['compile_hits']-before['compile_hits'],
            'compile_misses':metrics['compile_misses']-before['compile_misses'],
            'compile_seconds':metrics['compile_seconds']-before['compile_seconds']})
    frozen_wavefront.verify_files(pins)
    assert len({a['object_sha256'] for r in results for a in r['attempts']}) == 1
    assert calls == {'frontend':6, 'certificate':6}
    assert conn.execute('SELECT count(*) FROM attempts').fetchone()[0] == 6
    assert conn.execute('SELECT count(*) FROM attempt_edges').fetchone()[0] == 4
    assert results[1]['compile_hits'] == 2
    report = {'kind':'same-job-real-MIPS-exact-replay','isolated_root':str(root),
        'source':str(source_path),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'pin_sha256':pin,'results':results,'gates':calls,'attempts':6,'edges':4,
        'live_campaign_modified':False,'model_calls':0,
        'scope':'three identical exact-source compilations per arm; not a campaign throughput estimate'}
    (report_dir/'fresh-compile-replay.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
