"""Freeze source-bound small-function evidence without copying a live database."""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from eval import campaign_state

ROOT = Path('/mnt/c/Code/gameDecomp')
RUN = ROOT/'eval/results/resume-pipeline-20260908'
OUT = Path(__file__).resolve().parent
raw = (RUN/'campaign.json').read_bytes()
p = json.loads(raw)
with closing(sqlite3.connect((RUN/p['store']).as_uri()+'?mode=ro', uri=True)) as conn:
    state = campaign_state._hydrate(conn, p)
(OUT/'pointer.json').write_bytes(raw)
groups = Counter(state.get('tu_index', {}).values())
graph = state.get('dependency_graph', {})
rows = []
for name, node in state['nodes'].items():
    size = node.get('size')
    if not isinstance(size, int) or not 0 < size <= 256:
        continue
    group = state.get('tu_index', {}).get(name)
    row = {'function': name, **node, 'cluster': group, 'cluster_functions': groups.get(group, 0) if group else 0,
           'callers': graph.get('callers', {}).get(name, []),
           'callees': graph.get('callees', {}).get(name, [])}
    rows.append(row)
data = {'checkpoint': p['commit'], 'pointer_sha256': hashlib.sha256(raw).hexdigest(),
        'definitions': {'small_bytes_max': 256, 'tiny_bytes_max': 128, 'small_cluster_members_max': 3},
        'rows': rows, 'pins': state['pins'], 'db': state['config']['db']}
(OUT/'inventory.json').write_text(json.dumps(data))
unresolved = [r for r in rows if r['status'] not in {'object_exact', 'integrated'}]
summary = {'checkpoint': p['commit'], 'small_total': len(rows), 'small_unresolved':len(unresolved),
           'status': dict(Counter(r['status'] for r in unresolved)),
           'tiny_unresolved':sum(r['size']<=128 for r in unresolved),
           'cluster_sizes':dict(Counter(r['cluster_functions'] for r in unresolved)),
           'no_known_call_edges':sum(not r['callers'] and not r['callees'] for r in unresolved)}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary))
