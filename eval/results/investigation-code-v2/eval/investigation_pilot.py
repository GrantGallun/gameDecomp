"""Paired DEV comparison: proposal repair versus integrated investigation.

Parents, seeds, model-call/output caps, code and toolchain are fixed before
either arm. Inspection calls count against the same model budget. Compiler
counts and wall time remain reported separately; this is not equal compute.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from eval import agentrepair, frozen_wavefront
from solver import llm


class SingleTransportProvider:
    provider_id = 'ollama-single-transport-pilot'

    def generate(self, request):
        return llm.generate(request.endpoint, request.model, request.prompt,
            timeout=request.timeout, think=request.think, num_thread=request.num_thread,
            temperature=request.temperature, num_predict=request.num_predict, seed=request.seed,
            prefill=request.prefill, response_schema=request.response_schema, transport_attempts=1)


def isolate(repo, directory, function):
    directory.mkdir(parents=True, exist_ok=False)
    for name in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile', 'symbol_addrs.txt',
                 'snowboardkids.yaml', 'snowboardkids.z64', 'build', 'undefined_syms_auto.txt', 'undefined_syms.txt'):
        path = repo / name
        if path.exists():
            (directory / name).symlink_to(path, target_is_directory=path.is_dir())
    ws = directory / 'nonmatchings' / function
    ws.mkdir(parents=True)
    for path in (repo / 'nonmatchings' / function).iterdir():
        if path.is_file() and (path.suffix == '.py' or path.name.startswith('target') or
                path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm', '.compiler-target.json'}):
            shutil.copy2(path, ws / path.name)
    return directory


def run(repo, baseline_db, sources, output, *, calls=3, tokens=2000, timeout=45):
    project = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True, exist_ok=False)
    endpoint, model = llm.host(), 'gpt-oss:20b'
    pins = frozen_wavefront.file_hashes(frozen_wavefront.code_paths(project))
    report = {'kind': 'paired-investigation-dev', 'status': 'running', 'calls_per_arm': calls,
              'output_tokens_per_call': tokens, 'cases': [], 'code_hashes': pins,
              'socket_timeout': timeout, 'transport_attempts': 1,
              'model_digest': frozen_wavefront.model_digest(endpoint, model),
              'regime': 'preselected exposed DEV; supplied headers; no reference function bodies',
              'integration_requested': False}
    for function, path in sources.items():
        agentrepair._refuse_frozen_heldout(project / 'eval/sets', function)
        source = Path(path).read_text()
        folder = output / function
        folder.mkdir()
        (folder / 'original.c').write_text(source)
        report['cases'].append({'function': function, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'arms': {}})
    agentrepair._atomic_json(output / 'report.json', report)
    for index, case in enumerate(report['cases']):
        function = case['function']
        folder = output / function
        source = (folder / 'original.c').read_text()
        # Same committed database snapshot for both arms, including WAL state.
        with sqlite3.connect(baseline_db.resolve().as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(folder / 'baseline.sqlite') as baseline:
                original.backup(baseline)
        arms = ('proposal', 'investigation') if index % 2 == 0 else ('investigation', 'proposal')
        for arm in arms:
            frozen_wavefront.verify_files(pins)
            db = folder / (arm + '.sqlite')
            shutil.copy2(folder / 'baseline.sqlite', db)
            # Compiler work stays on the Linux filesystem. Receipts and chosen
            # sources persist in output after the ephemeral workspace closes.
            with tempfile.TemporaryDirectory(prefix='investigation-pilot-') as temporary:
                private = isolate(repo, Path(temporary) / 'repo', function)
                started = time.monotonic()
                try:
                    receipt = agentrepair.run(repo=private, db=db, function=function, source=source,
                        source_parent_attempt_id=None, out=folder / (arm + '.json'),
                        best_source_out=folder / (arm + '.best.c'), model=model, endpoint=endpoint,
                        draws=1, depth=calls, beam=3, max_calls=calls, timeout=timeout, think='low',
                        num_thread=4, temperature=.35, num_predict=tokens, seed=20260910,
                        cache_dir=None, verbose=False, resilient=True, structured_output=True,
                        include_header_context=True, retry_invalid=True, investigate=arm == 'investigation',
                        provider=SingleTransportProvider())
                    result = receipt['result']
                    case['arms'][arm] = {k: result.get(k) for k in ('exact', 'calls_attempted', 'recorded_tokens',
                        'best_source_sha256', 'best_score_improved', 'semantic_validation', 'best_residual', 'investigation')}
                    with sqlite3.connect(db) as conn:
                        case['arms'][arm]['attempts'] = conn.execute('SELECT COUNT(*) FROM attempts WHERE run_id LIKE ?',
                            (receipt['run_id'] + '%',)).fetchone()[0]
                    artifacts = folder / (arm + '-artifacts')
                    artifacts.mkdir()
                    for path in (private / 'nonmatchings' / function).iterdir():
                        if path.is_file() and path.suffix in {'.o', '.s', '.json', '.c'}:
                            shutil.copy2(path, artifacts / path.name)
                except (ValueError, OSError, RuntimeError) as exc:
                    case['arms'][arm] = {'status': 'error', 'error': str(exc)}
                case['arms'][arm]['wall_seconds'] = round(time.monotonic() - started, 3)
            frozen_wavefront.verify_files(pins)
            agentrepair._atomic_json(output / 'report.json', report)
            print(json.dumps({'function': function, 'arm': arm, 'exact': case['arms'][arm].get('exact'),
                              'seconds': case['arms'][arm]['wall_seconds']}), flush=True)
    report['status'] = 'complete'
    agentrepair._atomic_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--baseline-db', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--calls', type=int, default=3)
    parser.add_argument('--tokens', type=int, default=2000)
    args = parser.parse_args()
    run(args.repo, args.baseline_db, json.loads(args.sources.read_text()), args.out,
        calls=args.calls, tokens=args.tokens)


if __name__ == '__main__':
    main()
