"""Capture pre-as1 evidence for two already-scored, pinned private sources.

This diagnostic never scores or promotes -S output. Each compiler invocation is
durably logged against its existing ordinary receipt, including failure.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from solver.uopt_diagnosis import _recipe_command

NAME = 'drawControllerPakFileDeleteConfirmOptions'
OLD = Path('/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace')
OUT = Path('/home/grant/decomp/experiments/direct-compiler-20260926/phases-v2')
HERE = ROOT / 'eval/results/direct-compiler-20260926'
SOURCES = {
    'baseline': (157772, '24a97e50a489ad6763031ecf1c23a43678e3a8de234578a6240f584614434a72'),
    'prior_swap': (157773, 'ff96ba272f9df927727187dc1b0cccdc9d6430ccf0d1bf6515e20dbe7a55eb3b'),
}

def digest(data):
    return hashlib.sha256(data).hexdigest()

def main():
    OUT.mkdir(parents=True, exist_ok=False)
    repo = isolate(OLD / 'repo', OUT / 'repo', NAME)
    command = _recipe_command(repo / 'nonmatchings' / NAME, repo)
    if not command or '-c' not in command:
        raise RuntimeError('expected recorded compile-only command')
    # The recipe wraps cc in asm-processor, which requires an ELF -o output.
    # These retained sources contain no GLOBAL_ASM; preserve the exact cc flags
    # while asking cc directly for its pre-as1 text.
    separators = [i for i, arg in enumerate(command) if arg == '--']
    if len(separators) != 2 or separators[0] != 3:
        raise RuntimeError('unexpected asm-processor recipe')
    command = [command[2], *[arg for arg in command[separators[1] + 1:] if arg != '-c']]
    records = []
    with sqlite3.connect((OLD / 'attempts.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        for label, (receipt, expected) in SOURCES.items():
            source, stored = conn.execute('SELECT source_code,source_sha256 FROM attempts WHERE id=?', (receipt,)).fetchone()
            # Both values are checked; no candidate inference from historical winners.
            actual = digest(source.encode())
            if actual != stored or actual != expected:
                raise RuntimeError(f'source identity mismatch: {label}: {actual}')
            if 'GLOBAL_ASM' in source or 'INCLUDE_ASM' in source:
                raise RuntimeError('source needs asm-processor transformation')
            raw = OUT / f'{label}.raw.c'
            src = OUT / f'{label}.c'
            raw.write_text(source)
            convert = subprocess.run([sys.executable, str(repo / 'tools/textconv.py'),
                str(repo / 'tools/charmap.txt'), str(raw), str(src)], capture_output=True, text=True)
            if convert.returncode:
                raise RuntimeError(convert.stderr)
            output = repo / f'{label}.s'
            if output.exists():
                raise RuntimeError('preexisting phase output')
            cmd = command + ['-S', str(src)]
            record = {'label': label, 'source_sha256': actual, 'ordinary_db': str(OLD / 'attempts.sqlite'),
                      'ordinary_receipt': receipt, 'command': cmd, 'cwd': str(repo), 'started': time.time()}
            with (OUT / 'invocations.jsonl').open('a') as log:
                log.write(json.dumps(dict(record, status='started')) + '\n')
            try:
                run = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=120)
                record.update(returncode=run.returncode, stderr=run.stderr, stdout=run.stdout)
                if run.returncode == 0 and output.is_file():
                    saved = OUT / f'{label}.pre-as1.s'
                    shutil.copy2(output, saved)
                    record.update(assembly=str(saved), assembly_sha256=digest(saved.read_bytes()))
                    (HERE / f'{label}.pre-as1.s').write_bytes(saved.read_bytes())
                    (HERE / f'{label}.c').write_text(source)
            except BaseException as exc:
                record['exception'] = repr(exc)
                raise
            finally:
                record['elapsed_seconds'] = time.time() - record['started']
                with (OUT / 'invocations.jsonl').open('a') as log:
                    log.write(json.dumps(dict(record, status='finished')) + '\n')
            records.append(record)
    (HERE / 'phase-report-v2.json').write_text(json.dumps(records, indent=2) + '\n')
    print(json.dumps(records, indent=2))

if __name__ == '__main__':
    main()
