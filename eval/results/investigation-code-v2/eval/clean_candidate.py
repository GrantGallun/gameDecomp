"""Preview readability edits, or explicitly verify them in a private WSL workspace."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import shutil
import sqlite3

from solver import readability


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--function', required=True)
    parser.add_argument('--output', type=Path, required=True,
                        help='New directory; existing directories are refused')
    parser.add_argument('--verify', action='store_true', help='Compile edits (WSL/Linux only)')
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--baseline-db', type=Path)
    parser.add_argument('--max-attempts', type=int, default=12)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.function):
        parser.error('function must be a C identifier')
    if args.max_attempts < 0:
        parser.error('max-attempts must be nonnegative')
    if args.verify and (args.repo is None or args.baseline_db is None):
        parser.error('--verify requires --repo and --baseline-db')
    from eval.agentrepair import _refuse_frozen_heldout
    _refuse_frozen_heldout(Path(__file__).resolve().parent / 'sets', args.function)
    source = args.source.read_text(encoding='utf-8')
    proposals = readability.proposals(source, args.function)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'original.c').write_text(source, encoding='utf-8')
    (output / 'best.c').write_text(source, encoding='utf-8')

    def save(report):
        pending = output / 'report.pending.json'
        pending.write_text(json.dumps(report, indent=2), encoding='utf-8')
        pending.replace(output / 'report.json')
        pending_source = output / 'best.pending.c'
        pending_source.write_text(report.get('best_source', source), encoding='utf-8')
        pending_source.replace(output / 'best.c')

    save({'status': 'unverified_preview', 'function': args.function,
          'original_sha256': readability.digest(source),
          'proposals': [asdict(p) for p in proposals], 'integration_requested': False})
    if not args.verify:
        print(f'{len(proposals)} unverified proposals: {output / "report.json"}')
        return 0
    # Preserve every artifact and receipt in this run, never in the campaign DB.
    from solver import workspace
    original = args.repo.resolve()
    repo = output / 'scratch-repo'
    repo.mkdir()
    for name in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile',
                 'symbol_addrs.txt', 'snowboardkids.yaml', 'snowboardkids.z64',
                 'build', 'undefined_syms_auto.txt', 'undefined_syms.txt'):
        path = original / name
        if path.exists():
            (repo / name).symlink_to(path, target_is_directory=path.is_dir())
    ws = repo / 'nonmatchings' / args.function
    ws.mkdir(parents=True)
    for path in (original / 'nonmatchings' / args.function).iterdir():
        if path.is_file() and (path.suffix == '.py' or path.name.startswith('target')
                or path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm'}):
            shutil.copy2(path, ws / path.name)
    db = sqlite3.connect(output / 'attempts.sqlite')
    try:
        # SQLite backup includes committed WAL state without copying a live file.
        baseline = sqlite3.connect(args.baseline_db.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            baseline.backup(db)
        finally:
            baseline.close()
        evaluation_index = 0
        def evaluate(candidate, parent):
            nonlocal evaluation_index
            # Stable artifact name keeps build-input certificate paths comparable.
            attempt = workspace.score(ws, repo, 'cleanup', candidate, conn=db,
                func=args.function, strategy='readability-v1', parent_attempt_id=parent)
            archive = output / 'evaluations' / f'{evaluation_index:03d}'
            archive.mkdir(parents=True)
            evaluation_index += 1
            (archive / 'proposed.c').write_text(candidate, encoding='utf-8')
            (archive / 'attempt.json').write_text(json.dumps(asdict(attempt), indent=2),
                                                  encoding='utf-8')
            for artifact in ws.glob('cleanup*'):
                if artifact.is_file():
                    shutil.copy2(artifact, archive / artifact.name)
            return attempt
        report = readability.clean(source, args.function, evaluate,
                                   max_attempts=args.max_attempts, checkpoint=save)
    finally:
        db.close()
    print(f'{report["status"]}: {report["accepted"]} edits accepted; {output / "report.json"}')
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
