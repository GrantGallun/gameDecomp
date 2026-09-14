"""Read selected current candidates only; save independent experiment inputs."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_state, residual_patterns
from eval.inline_regions import body

OUT = Path(__file__).resolve().parent / 'selection'
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
OUT.mkdir(parents=True, exist_ok=False)
pointer = json.loads((RUN / 'campaign.json').read_bytes())
pointer['store'] = str(RUN / pointer['store'])
campaign_state.atomic(OUT / 'checkpoint.json', pointer)
state = campaign_state.read(OUT / 'checkpoint.json')
sha = lambda data: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
candidates = [(name, node) for name, node in state['nodes'].items()
              if (node.get('size') or 0) >= 1024 and (node.get('score') or 0) >= 90
              and node.get('status') not in {'object_exact', 'integrated'}
              and (node.get('residual') or {}).get('compiled') and node.get('attempt_id')]
candidates.sort(key=lambda item: (-float(item[1].get('score') or 0),
                                 -int((item[1].get('semantic_validation') or {}).get('status') == 'passed'), item[0]))
chosen = candidates[:20]
ids = [node['attempt_id'] for _, node in chosen]
with closing(sqlite3.connect(f'file:{RUN / "campaign.sqlite"}?mode=ro', uri=True, timeout=15)) as conn:
    conn.row_factory = sqlite3.Row
    records = {row['id']: dict(row) for row in conn.execute(
        'SELECT id,func_addr,source_code,source_sha256,compiled,exact,diff_summary FROM attempts WHERE id IN ('
        + ','.join('?' for _ in ids) + ')', ids)}
rows, rejected = [], []
for name, node in chosen:
    row = records.get(node['attempt_id'])
    if not row or row['func_addr'] != node['address'] or row['source_sha256'] != node['source_sha256'] or sha(row['source_code']) != node['source_sha256']:
        rejected.append({'name': name, 'reason': 'source/address binding mismatch'})
        continue
    if not row['compiled'] or row['exact'] or not row['diff_summary']:
        rejected.append({'name': name, 'reason': 'missing nonexact compiled diff'})
        continue
    target = Path(state['config']['repo']) / 'nonmatchings' / name / 'target.s'
    raw = target.read_bytes()
    if sha(raw) != state['pins'].get(str(target)):
        rejected.append({'name': name, 'reason': 'target assembly pin mismatch'})
        continue
    lines = row['source_code'].splitlines()
    loops = [{'line': i + 1, 'text': line.strip(), 'context': '\n'.join(lines[max(0, i-2):i+5])}
             for i, line in enumerate(lines) if re.search(r'\b(?:for|while)\s*\(', line)]
    if not loops:
        rejected.append({'name': name, 'reason': 'no explicit loop'})
        continue
    analysis = residual_patterns.analyse([{'name': name, 'attempt_id': row['id'],
        'source_sha256': node['source_sha256'], 'size': node['size'], 'diff': row['diff_summary']}])
    metadata = {'name': name, 'checkpoint': pointer['commit'], 'address': node['address'],
        'size': node['size'], 'score': node['score'], 'status': node['status'],
        'semantic_status': (node.get('semantic_validation') or {}).get('status'),
        'attempt_id': row['id'], 'source_sha256': node['source_sha256'],
        'target_sha256': sha(raw), 'diff_sha256': sha(row['diff_summary']), 'loops': loops,
        'diff_families': analysis['patterns']['single_instruction_families'],
        'diff_totals': analysis['totals']}
    rows.append((metadata, row['source_code'], row['diff_summary'], raw, body(raw, name)))
rows.sort(key=lambda x: (-int(x[0]['semantic_status'] == 'passed'), -x[0]['score'], x[0]['name']))
for metadata, source, diff, target, assembly in rows[:3]:
    folder = OUT / metadata['name']
    folder.mkdir()
    (folder / 'selected.c').write_text(source)
    (folder / 'diff.txt').write_text(diff)
    (folder / 'target.s').write_bytes(target)
    (folder / 'target-body.s').write_text(assembly)
    campaign_state.atomic(folder / 'metadata.json', metadata)
summary = {'checkpoint': pointer['commit'], 'eligible': len(candidates), 'queried_selected_rows': len(ids),
           'shortlist': [x[0] for x in rows[:3]], 'other_loop_candidates': [x[0] for x in rows[3:]],
           'rejected': rejected, 'scope': 'Current selected candidates only; no reference C or live changes'}
campaign_state.atomic(OUT / 'selection.json', summary)
print(json.dumps(summary))
