"""One command for bounded fresh intake -> frozen repair -> durable checkpoints.

No reference function bodies are supplied to the solver. Blocked SDK/assembly
targets stay in the receipts; this command never integrates non-exact source.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval import gamewide_probe, frozen_wavefront, differential_wavefront


def run(*, repo: Path, db: Path, project: Path, output: Path,
        batches: int = 1, batch_size: int = 8, rounds: int = 3,
        exactness_only: bool = False) -> dict:
    if batches < 1 or batch_size < 1 or rounds < 0:
        raise ValueError('invalid batch budget')
    if output.exists():
        raise ValueError('use a new output receipt; intake checkpoints persist in the database')
    receipt = {'kind': 'bounded-gamewide-batch', 'status': 'running', 'batches': [],
               'configuration': {'batches': batches, 'batch_size': batch_size,
                                 'rounds': rounds, 'exactness_only': exactness_only},
               'whole_rom_verified': False, 'complete_c_decompilation': False}
    artifacts = output.with_name(output.stem + '-artifacts')
    differential_wavefront._atomic_json(output, receipt)
    for index in range(batches):
        intake_path = artifacts / f'{index:03d}.intake.json'
        intake = gamewide_probe.run(repo=repo, db=db, project=project,
                                    output=intake_path, limit=batch_size)
        row = {'intake': str(intake_path), 'status': intake['status'],
               'selection': intake['selection'],
               'intake_outcomes': [{k: n.get(k) for k in ('function', 'status', 'exact')}
                                   for n in intake['nodes']]}
        receipt['batches'].append(row)
        differential_wavefront._atomic_json(output, receipt)
        if intake['status'] != 'complete':
            receipt.update(status='error', error=intake.get('error'))
            break
        if not intake['selection']:
            receipt['status'] = 'no_unattempted_eligible_functions'
            break
        census_path = intake_path.with_name(intake_path.stem + '.census.json')
        if census_path.is_file():
            census = json.loads(census_path.read_text(encoding='utf-8'))
            names = tuple(n['function'] for n in census['dag']['nodes'])
            repair_path = artifacts / f'{index:03d}.repair.json'
            result = frozen_wavefront.run(project=project, repo=repo, db=db,
                census=census_path, output=repair_path, functions=names,
                rounds=rounds, exactness_only=exactness_only)
            row.update(repair=str(repair_path), repair_status=result['status'],
                       aggregate=result.get('aggregate'), error=result.get('error'))
            if result['status'] != 'complete':
                receipt.update(status='error', error=result.get('error'))
                break
        differential_wavefront._atomic_json(output, receipt)
    if receipt['status'] == 'running':
        receipt['status'] = 'complete'
    differential_wavefront._atomic_json(output, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repo', 'db', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--batches', type=int, default=1)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--exactness-only', action='store_true')
    result = run(**vars(parser.parse_args()))
    print(json.dumps(result, indent=2))
    return 1 if result['status'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
