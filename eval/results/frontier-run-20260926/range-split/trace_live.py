"""Private retained-node trace; no candidate edits or reference-source access."""
import difflib
import gc
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_state import read
from eval.campaign_workers import isolate
from solver import regalloc_signature, source_attribution, uopt_attribution, uopt_diagnosis, uopt_trace, workspace

NAME = 'drawControllerPakFileDeleteConfirmOptions'
RUN = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
REPO = Path('/home/grant/decomp/sbk1')
PRIVATE = Path('/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace')
OUT = ROOT / 'eval/results/frontier-run-20260926/range-split/TRACE.json'

if PRIVATE.exists():
    raise RuntimeError(f'private experiment already exists: {PRIVATE}')
state = read(RUN / 'campaign.json')
original = state['nodes'][NAME]
node = {key: original.get(key) for key in ('status', 'score', 'attempt_id', 'source_sha256', 'address')}
checkpoint = json.loads((RUN / 'campaign.json').read_text()).get('commit')
del original, state
gc.collect()
if node['status'] != 'pending':
    OUT.write_text(json.dumps({'status': 'skipped', 'reason': 'node no longer pending', 'node': node}, indent=2))
    raise SystemExit(0)
PRIVATE.mkdir(parents=True)
db = PRIVATE / 'attempts.sqlite'
conn = sqlite3.connect(db)
conn.executescript((ROOT / 'kb/schema.sql').read_text())
conn.execute('ATTACH DATABASE ? AS kb', ('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro',))
function_row = conn.execute('SELECT * FROM kb.functions WHERE addr=?', (node['address'],)).fetchone()
if not function_row:
    raise RuntimeError('function missing from KB')
conn.execute('INSERT INTO tus SELECT * FROM kb.tus WHERE id=?', (function_row[2],))
conn.execute('INSERT INTO functions SELECT * FROM kb.functions WHERE addr=?', (node['address'],))
conn.execute('ATTACH DATABASE ? AS native', (f'file:{RUN / "campaign.sqlite"}?mode=ro',))
ancestors = []
ancestor = node['attempt_id']
while ancestor is not None:
    row = conn.execute('SELECT id,parent_attempt_id,run_id FROM native.attempts WHERE id=?', (ancestor,)).fetchone()
    if row is None:
        raise RuntimeError(f'native ancestor {ancestor} missing')
    ancestors.append(row)
    ancestor = row[1]
for attempt_id, _, run_id in reversed(ancestors):
    if run_id:
        conn.execute('INSERT OR IGNORE INTO attempt_runs SELECT * FROM native.attempt_runs WHERE id=?', (run_id,))
    conn.execute('INSERT INTO attempts SELECT * FROM native.attempts WHERE id=?', (attempt_id,))
source, digest = conn.execute('SELECT source_code,source_sha256 FROM attempts WHERE id=?', (node['attempt_id'],)).fetchone()
if hashlib.sha256(source.encode()).hexdigest() != digest or digest != node['source_sha256']:
    raise RuntimeError('retained source identity mismatch')
conn.commit()
isolated = isolate(REPO, PRIVATE / 'repo', NAME)
ws = isolated / 'nonmatchings' / NAME
attempt = workspace.score(ws, isolated, NAME, source, conn=conn, func=NAME,
                          strategy='range-split-retained-trace', parent_attempt_id=node['attempt_id'],
                          relation='diagnostic-recompile', action='compile unchanged retained source',
                          run_id='range-split-trace-20260926')
if not attempt.compiled:
    raise RuntimeError(f'ordinary baseline did not compile: {attempt.compiler_stderr[:500]}')
baseline = {'score': attempt.score, 'exact': attempt.exact, 'receipt_id': attempt.receipt_id,
            'parent_attempt_id': node['attempt_id'], 'frontend': attempt.frontend,
            'source_attribution_status': (attempt.source_attribution or {}).get('status')}
if attempt.exact:
    result = {'status': 'skipped_trace_baseline_exact', 'checkpoint': checkpoint, 'node': node,
              'source_sha256': digest, 'baseline': baseline, 'private_db': str(db)}
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    raise SystemExit(0)
if abs(attempt.score - node['score']) > .002:
    raise RuntimeError(f'baseline diverged: current {node["score"]}, private {attempt.score}')
texts = uopt_diagnosis.traced_compile(ws, isolated, source,
                                      Path('/home/grant/decomp/tools-src/ido-trace/cc'), NAME)
if texts is None:
    result = {'status': 'trace_unavailable', 'checkpoint': checkpoint, 'node': node,
              'source_sha256': digest, 'baseline': baseline, 'private_db': str(db),
              'trace_compile_limit': 3}
else:
    for key, value in texts.items():
        (PRIVATE / f'{key}.txt').write_text(value)
    target_dump = (ws / 'target_object_dump_normalized.s').read_text()
    candidate_dump = (ws / f'{NAME}_object_dump_normalized.s').read_text()
    report = uopt_diagnosis.diagnose(target_dump, candidate_dump,
                                     texts['level5'], texts['level6'], texts['ugen'], NAME)
    result = {'status': 'diagnosed' if 'declined' not in report else 'attribution_declined',
              'checkpoint': checkpoint, 'node': node, 'source_sha256': digest,
              'baseline': baseline, 'diagnosis': report, 'private_db': str(db),
              'trace_compile_limit': 3, 'trace_bytes': {k: len(v) for k, v in texts.items()}}
    if report.get('first'):
        first = report['first']
        attr = uopt_attribution.attribute(candidate_dump, texts['level5'], texts['level6'], texts['ugen'], NAME)
        proc = uopt_trace.join(texts['level5'], texts['level6'])[NAME]
        target = regalloc_signature.parse(uopt_attribution.strip_padding(target_dump))
        candidate = regalloc_signature.parse(uopt_attribution.strip_padding(candidate_dump))
        matcher = difflib.SequenceMatcher(a=[x.shape() for x in target], b=[x.shape() for x in candidate],
                                          autojunk=False)
        line_rows = source_attribution.instructions_of(attempt.source_attribution)
        uses = []
        for op, a0, a1, b0, b1 in matcher.get_opcodes():
            if op != 'equal':
                continue
            for shift in range(a1-a0):
                ti, ci = a0+shift, b0+shift
                wanted = dict(target[ti].registers())
                seen = dict(candidate[ci].registers())
                for position, actual, lr in attr.operands.get(ci, []):
                    if lr != first['lr'] or position not in wanted or position not in seen:
                        continue
                    uses.append({'target_index': ti, 'candidate_index': ci, 'operand_position': position,
                                 'target_register': wanted[position], 'candidate_register': actual,
                                 'target_instruction': target[ti].text,
                                 'candidate_instruction': candidate[ci].text,
                                 'source_line': line_rows[ci] if ci < len(line_rows) else None})
        record = proc.ranges[first['lr']]
        result['first_range'] = {'lr': record.lr, 'node': record.node, 'kind': record.kind,
                                 'offset': record.offset, 'color': record.color,
                                 'blocks': record.blocks, 'default_blocks': sorted(record.default_blocks),
                                 'block_flags': record.block_flags, 'hasstore': record.hasstore,
                                 'splits_in_trace': proc.splits, 'uses': uses}
conn.execute('CREATE TABLE IF NOT EXISTS trace_diagnostics (baseline_attempt_id INTEGER, source_sha256 TEXT, status TEXT, report_json TEXT, trace_compiles_max INTEGER)')
conn.execute('INSERT INTO trace_diagnostics VALUES (?,?,?,?,?)',
             (attempt.receipt_id, digest, result['status'], json.dumps(result.get('diagnosis')),
              3 if texts else 3))
conn.commit()
OUT.write_text(json.dumps(result, indent=2, default=str))
print(json.dumps({'status': result['status'], 'checkpoint': checkpoint, 'baseline': baseline,
                  'first': (result.get('diagnosis') or {}).get('first'),
                  'uses': (result.get('first_range') or {}).get('uses'),
                  'private_db': str(db)}, indent=2, default=str))
