"""Pinned development-only census; no sealed sources or reference C bodies."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/sbk1-dev-bottlenecks-20261002')
sys.path.insert(0, str(PROJECT))
from eval import seal, seal_run
from solver import signals, narrow_update, project_headers

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
manifest_path = PROJECT / 'eval/sets/sbk1_v5_sealed_nearmiss.json'
manifest = json.loads(manifest_path.read_text())
assert seal.audit(manifest)['clean']
dev = manifest['dev']
seal.assert_dev_only([r['function'] for r in dev], manifest)
assert not {r['tu'] for r in dev} & seal.sealed_tus_in_sets()
WORK.mkdir(parents=True, exist_ok=False)
inputs = WORK / 'inputs'
inputs.mkdir()
ledgers = {k: sqlite3.connect(f'file:{v}?mode=ro', uri=True) for k, v in seal_run.LEDGER_PATHS.items()}
for conn in ledgers.values():
    conn.row_factory = sqlite3.Row
rows = []
axes = ('structural', 'regalloc', 'ordering', 'offset', 'width', 'immediate', 'reloc')
for plan in dev:
    name, start = plan['function'], plan['start']
    row = ledgers[start['ledger']].execute('SELECT a.*, f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE a.id=?',
                                         (start['attempt_id'],)).fetchone()
    assert row['name'] == name and row['compiled'] == 1
    source = row['source_code']
    assert hashlib.sha256(source.encode()).hexdigest() == start['source_sha256']
    (inputs / (name + '.c')).write_text(source)
    (inputs / (name + '.diff')).write_text(row['diff_summary'] or '')
    sig = signals.analyse(row['diff_summary'] or '', row['score'] or 0, row['exact'] == 1, True)
    values = {a: getattr(sig, a) for a in axes}
    total = sum(values.values())
    dominant = max(values, key=values.get)
    group = 'no_instruction_diff' if not total else dominant if values[dominant] > total / 2 else 'mixed'
    proposals = list(narrow_update.variants(source, name))
    current_exact = {}
    for label, conn in ledgers.items():
        count = conn.execute('SELECT COUNT(*) FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE f.name=? AND a.exact=1',
                             (name,)).fetchone()[0]
        if count:
            current_exact[label] = count
    masked = project_headers._mask_noncode(source)
    rows.append({'function': name, 'tu': plan['tu'], 'start': start, 'score': row['score'],
        'source': str(inputs / (name + '.c')), 'diff': str(inputs / (name + '.diff')),
        'signals': asdict(sig), 'dominant': group, 'current_exact_receipts': current_exact,
        'narrow_proposals': [p[0] for p in proposals],
        'features': {'signed_narrow_locals': bool(re.search(r'\bs(?:8|16)\s+\w+\s*;', masked)),
            'full_width_mask': bool(re.search(r'&\s*(?:0xFFFF|0xFF|65535|255)\b', masked, re.I)),
            'loops': bool(re.search(r'\b(?:for|while|do)\b', masked)),
            'unsigned_narrow_views': bool(re.search(r'\(u(?:8|16)\s*\*\)', masked))}})
active = [r for r in rows if not r['current_exact_receipts']]
out = {'game': 'sbk1', 'scope': 'development only; historical exposure; stored-diff observations, not cause proofs',
    'manifest_digest': manifest['manifest_digest'], 'sealed_source_rows_read': 0,
    'development_functions': len(rows), 'still_unmatched_development_functions': len(active),
    'already_exact_development_functions': len(rows) - len(active),
    'dominant_counts': dict(Counter(r['dominant'] for r in active)),
    'axis_totals': {a: sum(r['signals'][a] for r in active) for a in axes},
    'narrow_applicable_unmatched': [r['function'] for r in active if r['narrow_proposals']],
    'narrow_applicable_all': [r['function'] for r in rows if r['narrow_proposals']],
    'feature_counts': {k: sum(r['features'][k] for r in active) for k in rows[0]['features']},
    'code_pins': {p: sha(PROJECT / p) for p in ('solver/narrow_update.py', 'solver/signals.py', 'eval/seal.py')},
    'runner_sha256': sha(__file__), 'rows': rows}
(HERE / 'census.json').write_text(json.dumps(out, indent=2) + '\n')
(WORK / 'census.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps({k: v for k, v in out.items() if k not in {'rows', 'code_pins'}}), flush=True)
for conn in ledgers.values():
    conn.close()
