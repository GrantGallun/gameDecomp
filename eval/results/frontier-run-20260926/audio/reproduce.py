"""One private audioThreadMain search from the native retained campaign candidate.

Uses the production scorer, frontend/certificate gates and ordinary mutation
stream. No reference or earlier experiment candidate is loaded.
"""
import collections
import gc
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

FROZEN = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908/code')
ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_state import read
from eval.campaign_workers import isolate
from eval.tool_agent_run import _attempt_to_verdict
from solver import diffrepair, regalloc_mutations, rodata_symbol, workspace

NAME = 'audioThreadMain'
REPO = Path('/home/grant/decomp/sbk1')
RUN = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
PRIVATE = Path('/home/grant/decomp/experiments/frontier-run-20260926/audio')
REPORT = ROOT / 'eval/results/frontier-run-20260926/audio/receipt.json'
RUN_ID = 'frontier-audio-reproduction-20260926'
PER_STEP, BUDGET, RESTART, RESTARTS = 8, 40, 16, 2


def private_db(address):
    PRIVATE.mkdir(parents=True, exist_ok=True)
    path = PRIVATE / 'attempts.sqlite'
    if path.exists():
        raise RuntimeError('private DB already exists; refusing to reuse an experiment')
    conn = sqlite3.connect(path)
    conn.executescript((FROZEN / 'kb/schema.sql').read_text())
    conn.execute('ATTACH DATABASE ? AS kb', (f'file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
    row = conn.execute('SELECT * FROM kb.functions WHERE addr=?', (address,)).fetchone()
    if row is None:
        raise RuntimeError('function missing from KB')
    tu_id = row[2]
    conn.execute('INSERT INTO tus SELECT * FROM kb.tus WHERE id=?', (tu_id,))
    conn.execute('INSERT INTO functions SELECT * FROM kb.functions WHERE addr=?', (address,))
    conn.commit()
    conn.close()
    return path


def verdict(attempt):
    result = _attempt_to_verdict(attempt)
    result['complete'] = workspace.repair_complete(attempt)
    return result


def score(source, kind, label, parent_id=None, iteration=0):
    conn = sqlite3.connect(DB, timeout=300)
    try:
        att = workspace.score(
            WS, ISO, NAME, source, conn=conn, func=NAME,
            strategy=f'audio-reproduction:{kind}:{label}'[:120],
            run_id=RUN_ID, iteration=iteration, parent_attempt_id=parent_id,
            relation='derive' if parent_id is not None else '',
            action=f'{kind}:{label}'[:120],
        )
        conn.commit()
    finally:
        conn.close()
    result = verdict(att)
    print(json.dumps({'iteration': iteration, 'kind': kind, 'label': label,
                      'receipt': att.receipt_id, 'score': result['score'],
                      'compiled': result['compiled'], 'exact': result['exact'],
                      'frontend_passed': (result.get('frontend') or {}).get('passed'),
                      'complete': result['complete']}), flush=True)
    return result


def proposals(source, parent, fired):
    output = []
    try:
        for label, candidate in rodata_symbol.variants(
                source, NAME, parent.get('diff') or '', parent.get('source_attribution'),
                elf=REPO / 'build/snowboardkids.elf', map_text=MAP_TEXT,
                target_obj=WS / 'target.o'):
            output.append((label, 'rodata_symbol', candidate))
    except Exception as exc:
        fired['rodata_symbol-crash'] += 1
        print('rodata_symbol decline', repr(exc), flush=True)
    try:
        for label, kind, candidate in regalloc_mutations.variants(
                source, NAME, parent.get('diff') or '', evidence=parent):
            output.append((label, kind, candidate))
    except Exception as exc:
        fired['stream-decline'] += 1
        print('mutation stream decline', repr(exc), flush=True)
    try:
        code, changed, _ = diffrepair.repair(source, parent.get('diff') or '')
        if changed and code != source:
            after_rodata = len([item for item in output if item[1] == 'rodata_symbol'])
            output.insert(after_rodata, ('diffrepair', 'diffrepair', code))
    except Exception as exc:
        fired['diffrepair-crash'] += 1
        print('diffrepair decline', repr(exc), flush=True)
    return output


def climb(source, parent, budget, seen, path, fired, start_iteration):
    used = 0
    best_source, best = source, parent
    while used < budget and not best['complete']:
        children = []
        for label, kind, candidate in proposals(best_source, best, fired):
            if candidate in seen:
                continue
            seen.add(candidate)
            children.append((label, kind, candidate))
            fired[kind] += 1
            if len(children) >= PER_STEP:
                break
        if not children:
            break
        step = None
        for label, kind, candidate in children:
            if used >= budget:
                break
            v = score(candidate, kind, label, best['receipt_id'], start_iteration + used)
            used += 1
            if not v['compiled']:
                continue
            key = (v['complete'], v['exact'], v['score'] or 0)
            if step is None or key > step[0]:
                step = (key, candidate, v, label, kind)
        current = (best['complete'], best['exact'], best['score'] or 0)
        if step is None or step[0] <= current:
            break
        best_source, best = step[1], step[2]
        path.append({'kind': step[4], 'label': step[3], 'score': best['score'],
                     'exact': best['exact'], 'complete': best['complete'],
                     'receipt': best['receipt_id'],
                     'source_sha256': hashlib.sha256(best_source.encode()).hexdigest()})
    return best_source, best, used


state = read(RUN / 'campaign.json')
full_node = state['nodes'][NAME]
node = {key: full_node[key] for key in ('status', 'verification', 'address', 'attempt_id', 'score')}
node['frontier'] = [{'source_sha256': full_node['frontier'][0]['source_sha256']}]
del full_node, state
gc.collect()
if node['status'] != 'pending' or node['verification'] is not None:
    raise RuntimeError('native node is no longer pending; re-audit before search')
native = sqlite3.connect(f'file:{RUN / "campaign.sqlite"}?mode=ro', uri=True)
row = native.execute('SELECT source_code,source_sha256 FROM attempts WHERE id=?',
                     (node['attempt_id'],)).fetchone()
native.close()
if row is None:
    raise RuntimeError('native retained attempt missing')
source, source_hash = row
if hashlib.sha256(source.encode()).hexdigest() != source_hash:
    raise RuntimeError('native source hash mismatch')
if node['frontier'][0]['source_sha256'] != source_hash:
    raise RuntimeError('frontier source differs from retained attempt')

DB = private_db(node['address'])
ISO = isolate(REPO, PRIVATE / 'isolate', NAME)
WS = ISO / 'nonmatchings' / NAME
MAP_TEXT = (REPO / 'build/snowboardkids.map').read_text(errors='replace')
base = score(source, 'baseline', 'retained-native', None, 0)
if not base['compiled'] or abs(base['score'] - node['score']) > 0.01:
    raise RuntimeError(f'baseline diverged from native score: {base["score"]} versus {node["score"]}')

first_diff = next((line for line in base['diff'].splitlines() if line.startswith('@@')), '')
fired, path, seen = collections.Counter(), [], {source}
best_source, best, used = climb(source, base, BUDGET, seen, path, fired, 1)
for _ in range(RESTARTS):
    if best['complete']:
        break
    path.append({'restart': True})
    best_source, best, more = climb(best_source, best, RESTART, seen, path, fired, 1 + used)
    used += more

receipt = {
    'function': NAME, 'native_checkpoint': json.loads((RUN / 'campaign.json').read_text()).get('commit'),
    'native_attempt': node['attempt_id'], 'native_source_sha256': source_hash,
    'native_score': node['score'], 'baseline_score': base['score'],
    'baseline_receipt': base['receipt_id'], 'baseline_first_diff_hunk': first_diff,
    'proposal_compiles': used, 'total_compiles': used + 1, 'path': path,
    'fired': dict(fired), 'best_score': best['score'], 'best_exact': best['exact'],
    'best_frontend': best.get('frontend'), 'best_complete': best['complete'],
    'best_receipt': best['receipt_id'],
    'best_source_sha256': hashlib.sha256(best_source.encode()).hexdigest(),
    'verification': best.get('verification'), 'private_db': str(DB),
    'private_workspace': str(WS),
}
REPORT.write_text(json.dumps(receipt, indent=2, default=str))
print('FINAL', json.dumps({k: v for k, v in receipt.items()
                           if k not in ('verification', 'best_frontend')}), flush=True)
