"""Read-only campaign failure inventory; run under WSL to avoid checkpoint locks."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from solver.modelrepair import _objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    db = sqlite3.connect((args.run / 'campaign.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    rows = db.execute('SELECT id,status,raw_response,prompt_context FROM model_proposals ORDER BY id').fetchall()
    db.close()
    statuses = Counter(row[1] for row in rows)
    shapes = Counter()
    examples = {}
    for row_id, status, raw, prompt in rows:
        if status != 'invalid':
            continue
        value = next(_objects(raw), None)
        labels = []
        if isinstance(value, dict) and isinstance(value.get('edits'), list):
            for edit in value['edits']:
                if not isinstance(edit, dict):
                    continue
                if edit.get('old', '') == '' and edit.get('slot', '') == '':
                    labels.append('missing_location')
                if edit.get('old') and edit.get('old') == edit.get('new'):
                    labels.append('literal_noop')
        for label in set(labels):
            shapes[label] += 1
            examples.setdefault(label, row_id)
    state = json.loads((args.run / 'campaign.json').read_text())
    near_misses = []
    for function, node in state['nodes'].items():
        if node.get('status') == 'object_exact' or (node.get('score') or 0) < 95:
            continue
        semantic = node.get('semantic_validation') or {}
        verification = node.get('verification') or {}
        near_misses.append({'function': function, 'score': node['score'],
            'status': node['status'], 'attempt_id': node.get('attempt_id'),
            'source_sha256': node.get('source_sha256'),
            'semantic_status': semantic.get('status'),
            'verification_status': verification.get('status'),
            'verification_kind': verification.get('kind'),
            'source': node.get('source'),
            'last_receipt': (node.get('jobs') or [{}])[-1].get('receipt')})
    near_misses.sort(key=lambda n: (-n['score'], n['function']))
    sample = next((r for r in rows if r[0] == 1007), None)
    report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'read-only development audit; database and checkpoint sampled separately',
        'proposal_max_id': max((r[0] for r in rows), default=0),
        'proposal_count': len(rows), 'proposal_statuses': dict(statuses),
        'invalid_percent': 100 * statuses['invalid'] / max(1, len(rows)),
        'invalid_response_shapes': dict(shapes), 'shape_examples': examples,
        'shape_scope': 'nonexclusive syntactic observations, not a complete replay of validator reasons',
        'states': dict(Counter(n['status'] for n in state['nodes'].values())),
        'high_score_nonexact_count': len(near_misses),
        'score_100_not_object_exact': sum(n['score'] == 100 for n in near_misses),
        'high_score_nonexact': near_misses}
    if sample:
        report['proposal_1007'] = {'status': sample[1], 'prompt_chars': len(sample[3]),
            'slot_table_offset': sample[3].find('EDITABLE SOURCE SLOTS'),
            'rejected_proposal_present': 'Rejected proposal (not applied)' in sample[3]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as out:
        json.dump(report, out, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != 'high_score_nonexact'}, indent=2))


if __name__ == '__main__':
    main()
