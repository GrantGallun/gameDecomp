from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import campaign_state
from eval.inline_regions import digest, body

OUT = Path(__file__).resolve().parent / 'selection'
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
state = campaign_state.read(OUT / 'checkpoint.json')
pool = [(name, node) for name, node in state['nodes'].items()
        if (node.get('size') or 0) >= 1024 and (node.get('score') or 0) >= 75
        and node.get('status') not in {'object_exact', 'integrated'}
        and (node.get('residual') or {}).get('compiled') and node.get('attempt_id')]
pool.sort(key=lambda item: (-item[1]['score'], item[0]))
pool = pool[:45]
ids = [node['attempt_id'] for _, node in pool]
with closing(sqlite3.connect(f'file:{RUN / "campaign.sqlite"}?mode=ro', uri=True, timeout=15)) as conn:
    conn.row_factory = sqlite3.Row
    rows = {row['id']: dict(row) for row in conn.execute(
        'SELECT id,func_addr,source_code,source_sha256,diff_summary FROM attempts WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
results = []
for name, node in pool:
    row = rows[node['attempt_id']]
    assert row['func_addr'] == node['address'] and row['source_sha256'] == node['source_sha256'] == digest(row['source_code'])
    lines = row['source_code'].splitlines()
    cues = [{'line': i+1, 'text': line.strip()} for i, line in enumerate(lines)
            if re.search(r'\b(?:for|while|goto)\b|\+=|\+\+|\*var_|\[[a-zA-Z_][^]]*\]', line)]
    pointer_cues = [cue for cue in cues if '+=' in cue['text'] or '++' in cue['text'] or '[' in cue['text']]
    metadata = {'name': name, 'score': node['score'], 'size': node['size'], 'address': node['address'],
                'attempt_id': node['attempt_id'], 'source_sha256': node['source_sha256'],
                'semantic_status': (node.get('semantic_validation') or {}).get('status'),
                'cues': cues, 'checkpoint': 6136}
    if pointer_cues:
        folder = OUT / 'traversal-pool' / name
        folder.mkdir(parents=True, exist_ok=False)
        target = Path(state['config']['repo']) / 'nonmatchings' / name / 'target.s'
        raw = target.read_bytes()
        assert digest(raw) == state['pins'].get(str(target))
        metadata.update(target_sha256=digest(raw), diff_sha256=digest(row['diff_summary']))
        (folder / 'source.c').write_text(row['source_code'])
        (folder / 'target.s').write_bytes(raw)
        (folder / 'target-body.s').write_text(body(raw, name))
        (folder / 'diff.txt').write_text(row['diff_summary'])
        campaign_state.atomic(folder / 'metadata.json', metadata)
        results.append(metadata)
campaign_state.atomic(OUT / 'traversal-pool.json', results)
print(json.dumps([{key: row[key] for key in ('name','score','size','semantic_status')} |
                 {'cues':row['cues'][:12]} for row in results]))
