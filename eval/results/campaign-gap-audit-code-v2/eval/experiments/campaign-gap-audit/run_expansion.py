"""Preselect untouched medium/large DEV functions, then run a bounded campaign.

Run from the fixed code snapshot. No historical candidate or reference body is
used to choose the panel. Holdout exclusions remain enforced by the controller.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from eval import agentrepair, completion_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project = Path.cwd()
    cohort_path = args.output.with_name(args.output.stem + '.cohort.json')
    if args.output.exists() or cohort_path.exists():
        raise ValueError('refusing to overwrite campaign or selection receipt')
    with sqlite3.connect(f'file:{args.db.as_posix()}?mode=ro', uri=True) as conn:
        cutoff = conn.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]
        rows = conn.execute('''SELECT f.name,f.insn_count,t.name FROM functions f
            JOIN tus t ON t.id=f.tu_id WHERE f.insn_count BETWEEN 65 AND 400
            AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.func_addr=f.addr)''').fetchall()
    seed = '20260905-campaign-gap-expansion-v1:'
    groups = {}
    excluded = []
    for name, count, tu in rows:
        try:
            agentrepair._refuse_frozen_heldout(project/'eval/sets', name)
        except ValueError:
            excluded.append(name)
            continue
        if not tu.startswith('build/src/'):
            continue  # this panel tests ordinary C intake; hardware tracked separately
        stratum = ('sdk' if '/ultra/' in tu else 'game') + ('_medium' if count <= 160 else '_large')
        groups.setdefault(stratum, []).append({'function': name, 'instructions': count, 'tu': tu,
            'selection_hash': hashlib.sha256((seed + name).encode()).hexdigest(), 'stratum': stratum})
    selected = [row for key in sorted(groups)
                for row in sorted(groups[key], key=lambda row: row['selection_hash'])[:2]]
    if len(selected) != 8 or len(groups) != 4:
        raise ValueError('insufficient untouched eligible functions for fixed strata')
    receipt = {'kind': 'preselected-development-expansion', 'seed': seed,
        'selection': 'two smallest stable hashes per game/SDK x 65-160/161-400 instructions; zero prior attempts',
        'attempt_cutoff': cutoff, 'excluded_heldout': sorted(excluded),
        'eligible_by_stratum': {key: len(value) for key, value in groups.items()}, 'dev': selected,
        'reference_bodies_used': False, 'historical_seeds_used': False}
    agentrepair._atomic_json(cohort_path, receipt)
    print(json.dumps(receipt, indent=2), flush=True)
    result = completion_campaign.run(project=project, repo=args.repo, db=args.db,
        state_path=args.output, functions=tuple(row['function'] for row in selected),
        model_calls=0, max_work_items=16)
    print(json.dumps({'status': result['status'], 'summary': result.get('summary')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
