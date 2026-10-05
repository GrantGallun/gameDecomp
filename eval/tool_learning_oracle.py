"""Trusted laboratory compiler broker. Generated tools never receive this object."""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from eval.tool_sandbox import MAX_SOURCE, execute, namespace_command
from solver import byte_certificate

PRELUDE = ('typedef signed char s8; typedef unsigned char u8;\n'
           'typedef short s16; typedef unsigned short u16;\n'
           'typedef int s32; typedef unsigned int u32;\n')
FLAGS = ('-c', '-O2', '-mips1', '-G0', '-non_shared')


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_source(source):
    # Small laboratory domain. Expanding it requires another boundary review.
    if (not isinstance(source, str) or not 1 <= len(source.encode()) <= MAX_SOURCE
            or not source.isascii() or any(c in source for c in '#"\'\\?\x00')
            or '%:' in source or '/*' in source or '//' in source
            or re.search(r'\b(?:asm|__asm__|__asm|INCLUDE_ASM|GLOBAL_ASM|_Pragma)\b', source)):
        raise ValueError('plain bounded C only; no directives, strings, comments or assembly')


class Oracle:
    def __init__(self, compiler: Path, cases: list[dict], *, timeout=20):
        self.compiler = compiler.resolve(strict=True)
        self.timeout = timeout
        self.targets = {c['id']: Path(c['target']).resolve(strict=True) for c in cases}
        self.pins = self.identity()
        self.recipe = {'flags': FLAGS, 'prelude_sha256': sha(PRELUDE.encode()),
                       'compiler_files': self.pins['compiler'],
                       'certificate_sha256': sha(Path(byte_certificate.__file__).read_bytes())}
        self.recipe_sha256 = sha(json.dumps(self.recipe, sort_keys=True).encode())

    def identity(self):
        return {'compiler': {p.name: sha(p.read_bytes()) for p in sorted(self.compiler.iterdir())
                             if p.is_file()},
                'targets': {key: sha(p.read_bytes()) for key, p in self.targets.items()}}

    def check_integrity(self):
        if self.identity() != self.pins:
            raise RuntimeError('shared compiler or target identity changed; results quarantined')

    @staticmethod
    def listing(path, deadline=None):
        remaining = min(5, deadline - time.monotonic()) if deadline is not None else 5
        if remaining <= 0:
            raise TimeoutError('case deadline reached before disassembly')
        p = subprocess.run(['mips-linux-gnu-objdump', '-dr', str(path)],
                           capture_output=True, text=True, check=True, timeout=remaining)
        return '\n'.join(p.stdout.splitlines()[4:])[-24000:]

    def compile(self, source, output: Path, *, deadline=None):
        validate_source(source)
        output = output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='tool-compile-', dir=output.parent) as directory:
            work = Path(directory)
            (work / 'candidate.c').write_text(PRELUDE + source, encoding='utf-8')
            command = [*namespace_command(), '--ro-bind', str(self.compiler), '/compiler',
                       '--bind', str(work), '/work', '--remount-ro', '/', '--remount-ro', '/dev',
                       '/compiler/cc', *FLAGS,
                       '-o', '/work/candidate.o', '/work/candidate.c']
            # The recompiled IDO tools reserve guest address space; this is a
            # compiler-only virtual-memory ceiling, not the generated-tool limit.
            remaining = min(self.timeout, deadline - time.monotonic()) if deadline is not None else self.timeout
            if remaining <= 0:
                raise TimeoutError('case deadline reached before compilation')
            result = execute(command, timeout=remaining, memory_bytes=8 * 1024**3)
            obj = work / 'candidate.o'
            if result['status'] == 'ok' and obj.is_file():
                output.write_bytes(obj.read_bytes())
                return {**result, 'compiled': True}
            # Bwrap/resource/toolchain failures are inconclusive, not C rejection.
            rejected = (result['status'] == 'process_error' and result['returncode'] > 0
                        and ('cfe:' in result['stderr'] or 'cc: Error:' in result['stderr']))
            return {**result, 'compiled': False,
                    'status': 'compiler_rejected' if rejected else 'infra_error'}

    def score(self, case, source, *, deadline=None):
        start = time.monotonic()
        base = {'source_sha256': sha(source.encode()), 'compiled': False, 'exact': False,
                'compiles': 0, 'recipe_sha256': self.recipe_sha256}
        try:
            self.check_integrity()
            validate_source(source)
            with tempfile.TemporaryDirectory(prefix='tool-score-') as directory:
                obj = Path(directory) / 'candidate.o'
                base['compiles'] = 1
                built = self.compile(source, obj, deadline=deadline)
                base.update(built)
                if built['compiled']:
                    target = self.targets[case['id']]
                    certificate = byte_certificate.certify(target, obj, source=PRELUDE + source,
                                     build_inputs={'recipe_sha256': self.recipe_sha256})
                    expected = self.pins['targets'][case['id']]
                    if certificate.get('target_sha256') != expected:
                        raise RuntimeError('target changed during certification')
                    target_asm, candidate_asm = self.listing(target, deadline), self.listing(obj, deadline)
                    base.update(certificate=certificate, exact=certificate['exact'],
                                status='ok' if certificate['status'] != 'unverified' else 'infra_error',
                                diff='\n'.join(difflib.unified_diff(target_asm.splitlines(),
                                          candidate_asm.splitlines(), fromfile='target', tofile='candidate')),
                                target_asm=target_asm, candidate_asm=candidate_asm)
            self.check_integrity()
        except ValueError as exc:
            base.update(status='source_rejected', error=str(exc), exact=False)
        except Exception as exc:
            base.update(status='infra_error', error=f'{type(exc).__name__}: {exc}', exact=False)
        return {**base, 'seconds': time.monotonic() - start}
