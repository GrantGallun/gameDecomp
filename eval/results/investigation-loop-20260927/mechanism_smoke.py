"""Ordinary native compiler and execution controls on snapshotted candidates."""
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path('/mnt/c/Code/gameDecomp')
NATIVE = Path('/home/grant/decomp/experiments/investigation-loop-20260927')
REPO = Path('/home/grant/decomp/sbk1')
sys.path.insert(0, str(ROOT))
from eval import campaign_workers
from eval.semantic_lane import DeferredPanel
from solver import workspace, compiler_experiment, execution_experiment, modelrepair

manifest = json.loads((NATIVE / 'manifest.json').read_text())
reports = []
with sqlite3.connect(NATIVE / 'canary.sqlite') as conn:
    for row in [manifest['selected'][1], manifest['selected'][2]]:
        name, node = row['function'], row['node']
        source = Path(node['source']).read_text()
        workspace.bootstrap(REPO, name)
        repo = campaign_workers.isolate(REPO, NATIVE / 'smoke-repo', name)
        ws = workspace.bootstrap(repo, name)
        started = time.monotonic()
        tag = name + '_investigation_smoke'
        att = workspace.score(ws, repo, tag, source, conn=conn, func=name,
            parent_attempt_id=node['attempt_id'], relation='investigation-smoke',
            action='ordinary root reproduction', strategy='investigation-smoke')
        obj = ws / (tag + '.o') if att.compiled else None
        report = {'function': name, 'score': att.score, 'compiled': att.compiled,
                  'exact': workspace.repair_complete(att), 'source_parent': node['attempt_id']}
        report['phase'] = compiler_experiment.inspect(repo, ws, name, source,
            NATIVE / 'smoke-phases', hypothesis='observe active candidate before assembly scheduling',
            object_path=obj)
        panel = DeferredPanel(repo, ws, name, 8, 10000)
        report['semantic'] = panel(modelrepair.CandidateState(source, att, obj))
        runtime = execution_experiment.Tools(repo, ws, name, NATIVE / ('smoke-execution-' + name), panel=panel)
        report['execution'] = runtime.propose({}, source, obj)
        report['seconds'] = time.monotonic() - started
        reports.append(report)
        (NATIVE / 'mechanism-smoke.json').write_text(json.dumps(reports, indent=2) + '\n')
        print(json.dumps({'function': name, 'score': att.score, 'compiled': att.compiled,
            'phase_status': report['phase']['status'], 'comparable': report['phase']['comparable'],
            'phase_reason': report['phase'].get('reason'), 'execution': report['execution']['status'],
            'seconds': report['seconds']}), flush=True)
