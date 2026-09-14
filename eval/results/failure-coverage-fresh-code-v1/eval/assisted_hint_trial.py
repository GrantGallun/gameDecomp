"""Run one fixed-root, explicitly assisted development hint arm.

Uses the existing repair controller and its append-only attempt/proposal logs.
No reference source loading, campaign promotion, or solver configuration changes.
"""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from eval import agentrepair
from solver import llm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--function', required=True)
    parser.add_argument('--attempt-id', type=int, required=True)
    parser.add_argument('--hint', type=Path, action='append', default=[])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--max-calls', type=int, default=2)
    parser.add_argument('--continue-compiled', action='store_true',
                        help='continue residual repair after frontend/compile pass')
    parser.add_argument('--seed', type=int, default=20260905)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('refusing to overwrite an existing experiment receipt')
    agentrepair._refuse_frozen_heldout(Path('eval/sets'), args.function)
    with sqlite3.connect(args.db) as conn:
        source = agentrepair._source_for_attempt(conn, args.attempt_id, args.function)
    hint = '\n\n'.join(path.read_text(encoding='utf-8') for path in args.hint)
    provenance = {
        'classification': 'supervisor-assisted development; not heldout or autonomous',
        'hint_paths': [str(path) for path in args.hint],
        'hint_sha256': hashlib.sha256(hint.encode()).hexdigest(),
        'reference_function_body_used': False,
        'source_parent_attempt_id': args.attempt_id,
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    }
    receipt = agentrepair.run(
        repo=args.repo, db=args.db, function=args.function, source=source,
        source_parent_attempt_id=args.attempt_id, out=args.out,
        best_source_out=args.out.with_suffix('.best.c'), model='gpt-oss:20b',
        endpoint=llm.host(), draws=1, depth=args.max_calls, beam=1,
        max_calls=args.max_calls, timeout=420, think='low', num_thread=12,
        temperature=0.35, num_predict=10000, seed=args.seed,
        cache_dir=None, verbose=True, strategy_brief=hint,
        structured_output=True, retry_invalid=True,
        include_header_context=True, compile_only=not args.continue_compiled, type_transaction=True)
    # Supplemental provenance belongs to this experiment, not solver evidence.
    agentrepair._atomic_json(args.out.with_suffix('.provenance.json'), provenance)
    print(json.dumps(receipt['result'], indent=2))


if __name__ == '__main__':
    main()
