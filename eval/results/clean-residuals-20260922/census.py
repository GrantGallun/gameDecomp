"""Fresh diagnostics on the exact 200 post-member-repair incumbents."""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.intake_probe import classify_residual
from solver import frontend_diagnostics

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent / 'clean-members-20260922'
paired = json.loads((PRIOR / 'paired-final.json').read_text())
targets = {r['function']: r['target'] for r in json.loads((PRIOR / 'census.json').read_text())['rows']}
repo = Path.home() / 'decomp/sbk1'
rows, messages, classes = [], Counter(), Counter()
message_states = defaultdict(set)
for entry in paired['rows']:
    name = entry['function']
    source = (PRIOR / 'states' / name / 'final.c').read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert digest == entry['after']['source_sha256']
    front = frontend_diagnostics.analyse(source, repo=repo, target=targets[name], full_diagnostics=True)
    assert front['error_count'] == entry['after']['errors'], name
    assert front['status'] == entry['after']['frontend'], name
    folder = OUT / 'states' / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'before.c').write_text(source)
    (folder / 'before-frontend.json').write_text(json.dumps(front, indent=2) + '\n')
    counts = Counter(classify_residual(e['what']) for e in front['errors'])
    classes.update(counts.keys())
    for error in front['errors']:
        key = re.sub(r"'[^']*'", "'<value>'", error['what'])
        messages[key] += 1
        message_states[key].add(name)
    rows.append(dict(function=name, target=targets[name], source_sha256=digest,
        compiled=entry['after']['compiled'], exact=entry['after']['exact'], score=entry['after']['score'],
        frontend=front['status'], errors=front['error_count'], classes=dict(counts)))
    if len(rows) % 50 == 0:
        print(f'Rechecked {len(rows)}/200', flush=True)
report = dict(states=len(rows), classes=dict(classes.most_common()),
    messages=[dict(message=k, states=len(message_states[k]), errors=v) for k,v in messages.most_common()], rows=rows)
(OUT / 'census.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k:v for k,v in report.items() if k != 'rows'}, indent=2))
