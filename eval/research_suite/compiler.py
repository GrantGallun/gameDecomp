"""Native, isolated candidate compiles using existing project authorities."""
from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from solver import byte_certificate, compiler_experiment, compiler_recipe, frontend_check
from solver.regalloc_search import Compiled
from .manifest import digest, fingerprint, inside, write_json


def accepted(certificate, frontend, source, target_sha256):
    sha = digest(source.encode())
    return (certificate.get('schema_version') == 1
            and certificate.get('kind') == 'mips_object_section_certificate'
            and certificate.get('status') == 'object_sections_exact'
            and certificate.get('exact') is True
            and certificate.get('source_sha256') == sha
            and certificate.get('target_sha256') == target_sha256
            and bool(re.fullmatch('[0-9a-f]{64}', certificate.get('candidate_sha256', '')))
            and frontend.get('passed') is True and frontend.get('source_sha256') == sha)


def environment(repo, targets):
    repo = Path(repo).resolve()
    tool_paths = [p for p in (repo / 'tools/ido-recomp/linux').glob('*') if p.is_file()]
    tool_paths += [repo / 'tools/textconv.py', repo / 'tools/charmap.txt',
                   repo / 'tools/claude-decomp-env/objdump.py',
                   repo / 'tools/claude-decomp-env/normalize_asm.py']
    if not tool_paths or not (repo / 'tools/ido-recomp/linux/cc').is_file():
        raise ValueError('native IDO toolchain is unavailable')
    tools = {str(p.relative_to(repo)): digest(p.read_bytes()) for p in sorted(tool_paths)}
    headers = {str(p.relative_to(repo)): digest(p.read_bytes())
               for folder in ('include', 'src') for p in sorted((repo / folder).rglob('*'))
               if p.is_file() and (folder == 'include' or p.suffix in {'.h', '.inc'})}
    for executable in ('clang', 'mips-linux-gnu-objdump', 'mips-linux-gnu-nm', 'make'):
        path = shutil.which(executable)
        if not path:
            raise ValueError('required tool is unavailable: ' + executable)
        tools[executable] = digest(Path(path).read_bytes())
    project = Path(__file__).resolve().parents[2]
    # All Python solver code can participate in a generator; version it rather
    # than hashing only the top-level function that imports other families.
    code = {str(p.relative_to(project)): digest(p.read_bytes())
            for folder in ('solver', 'eval/research_suite')
            for p in sorted((project / folder).rglob('*.py'))}
    code['eval/agentrepair.py'] = digest((project / 'eval/agentrepair.py').read_bytes())
    return {'repo': str(repo), 'tools': tools, 'headers_sha256': fingerprint(headers),
            'recipes': {target: compiler_recipe.resolve(repo, target) for target in sorted(set(targets))},
            'implementation_sha256': fingerprint(code)}


def admit_source(source):
    from solver.toolagent import _validate_reconstruction
    _validate_reconstruction(source, source)
    if len(source.encode()) > 1_000_000:
        raise ValueError('candidate exceeds source size bound')
    # A literal include is the only admitted include form. Normalize comments
    # and splices so this boundary cannot be bypassed by splitting a directive.
    text = source.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\\\n', '', text)
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)
    text = tokens.sub(lambda m: ' ' + '\n' * m[0].count('\n')
                      if m[0].startswith(('/*', '//')) else m[0], text)
    for directive in re.findall(r'(?m)^\s*#\s*(?:include|include_next|import)\b([^\n]*)', text):
        match = re.fullmatch(r'\s*[<"]([^>"\n]+)[>"]\s*', directive)
        if not match:
            raise ValueError('unresolved include is not admitted')
        name = match[1].replace('\\', '/')
        if (name.startswith('/') or ':' in name or '..' in name.split('/')
                or Path(name).suffix not in {'.h', '.inc'}):
            raise ValueError('only relative header includes are admitted')


def _run(command, cwd, timeout=60):
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                          errors='replace', timeout=timeout)
    if proc.returncode:
        raise RuntimeError(f'command exited {proc.returncode}: ' + (proc.stdout + proc.stderr)[-8000:])
    return proc.stdout


def listing(repo, obj, work):
    raw = _run([sys.executable, str(Path(repo) / 'tools/claude-decomp-env/objdump.py'), str(obj)], work)
    raw_path = Path(work) / (Path(obj).stem + '.dump.s')
    raw_path.write_text(raw, encoding='utf-8')
    return _run([sys.executable, str(Path(repo) / 'tools/claude-decomp-env/normalize_asm.py'),
                 str(raw_path)], work)


def symbol_identity(symbol_text, function):
    symbols = []
    for line in symbol_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] in {'T', 't'}:
            symbols.append((parts[0], int(parts[2], 16)))
    # sbk1's assembly macros also emit a `<function>.NON_MATCHING` marker symbol at the function's own
    # address (nonmatchings/*/target.o: `f T 0 114` and `f.NON_MATCHING T 0 114`). That alias of the
    # requested function at offset 0 is the same binding; any other symbol, or offset, is still refused.
    symbols = [s for s in symbols if s != (f'{function}.NON_MATCHING', 0)]
    if symbols != [(function, 0)]:
        raise ValueError('object must expose the requested isolated function at text offset zero')
    return {'function': function, 'text_offset': 0, 'status': 'symbol_verified',
            'scope': 'function/object binding; original binary and function-to-TU provenance remain supplied inputs'}


class NativeCompiler:
    """One fresh compiler/receipt directory per task-arm-seed. No cache reuse."""

    def __init__(self, repo, task, bundle, output, *, budget, identity):
        self.repo, self.bundle = Path(repo).resolve(), Path(bundle).resolve()
        self.output, self.task = Path(output).resolve(), task
        if os.name != 'posix' or str(self.output).startswith('/mnt/'):
            raise ValueError('N64 experiments require native WSL/Linux output, outside /mnt')
        if type(budget) is not int or budget < 1:
            raise ValueError('compile budget must be a positive integer')
        self.output.mkdir(parents=True, exist_ok=False)
        self.budget, self.calls, self.key_calls = budget, 0, 0
        self.seconds = self.key_seconds = 0.0
        self.identity, self.rows = identity, []
        self.target = inside(self.bundle, task['target_object'])
        self.target_sha256 = digest(self.target.read_bytes())
        self.target_identity = symbol_identity(_run(['mips-linux-gnu-nm', '-g', '--defined-only',
                                                     '--format=posix', str(self.target)], self.output), task['function'])
        self.target_dump = listing(self.repo, self.target, self.output)
        (self.output / 'target.normalized.s').write_text(self.target_dump, encoding='utf-8')
        self.recipe = identity['recipes'][task['compile_target']]
        self.context = inside(self.bundle, task['context'])
        self.command = compiler_experiment._direct_command(self.recipe['command'], self.repo, self.context)
        self.key_workspace = self.output / 'key-workspace'
        self.key_workspace.mkdir()
        for path in self.context.rglob('*'):
            if path.is_file():
                dest = self.key_workspace / path.relative_to(self.context)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        write_json(self.key_workspace / '.compiler-target.json',
                   {'function': task['function'], 'target': task['compile_target']})

    def __call__(self, source, label, parent_source=None):
        if self.calls >= self.budget:
            raise ValueError('compiler budget exhausted')
        self.calls += 1
        started = time.monotonic()
        work = self.output / f'attempt-{self.calls:05d}'
        work.mkdir()
        row = {'id': self.calls, 'source_sha256': digest(source.encode()),
               'parent_sha256': digest(parent_source.encode()) if parent_source is not None else None,
               'label': label, 'compiled': False, 'exact': False, 'artifact': work.name,
               'target_sha256': self.target_sha256, 'compile_target': self.task['compile_target']}
        result = Compiled(False, False)
        raw = work / 'source.c'
        raw.write_text(source, encoding='utf-8')
        try:
            admit_source(source)
            wrapped = compiler_experiment._compile_source(self.repo, source)
            (work / 'wrapped.c').write_text(wrapped, encoding='utf-8')
            converted = work / 'candidate.c'
            _run([sys.executable, str(self.repo / 'tools/textconv.py'),
                  str(self.repo / 'tools/charmap.txt'), str(work / 'wrapped.c'), str(converted)], work)
            obj = work / 'candidate.o'
            row['command'] = [*self.command, '-o', str(obj), str(converted)]
            _run(row['command'], work)
            if not obj.is_file():
                raise ValueError('compiler did not produce an object')
            row['compiled'] = True
            row['function_identity'] = symbol_identity(_run(['mips-linux-gnu-nm', '-g', '--defined-only',
                                                            '--format=posix', str(obj)], work), self.task['function'])
            text = listing(self.repo, obj, work)
            (work / 'candidate_object_dump_normalized.s').write_text(text, encoding='utf-8')
            diff = ''.join(difflib.unified_diff(self.target_dump.splitlines(True), text.splitlines(True),
                                               'target', 'candidate'))
            (work / 'candidate_diff').write_text(diff, encoding='utf-8')
            cert = byte_certificate.certify(self.target, obj, source=source,
                                             build_inputs={'environment': fingerprint(self.identity)})
            frontend = self._frontend(raw, work)
            row.update(verification=cert, frontend=frontend,
                       exact=accepted(cert, frontend, source, self.target_sha256),
                       object_sha256=digest(obj.read_bytes()))
            attribution = self._attribution(work, source, wrapped, converted, obj, diff)
            evidence = {'compiled': True, 'verification': cert, 'frontend': frontend,
                        'compiler_recipe': self.recipe, 'source_attribution': attribution}
            row['source_attribution'] = attribution
            result = Compiled(True, row['exact'], text, diff, evidence, obj=obj.read_bytes())
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            row['seconds'] = time.monotonic() - started
            self.seconds += row['seconds']
            self.rows.append(row)
            write_json(work / 'receipt.json', row)
            with (self.output / 'attempts.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(row, allow_nan=False) + '\n')
        return result

    def _frontend(self, source, work):
        """Apply the existing strict project policy with the frozen local headers."""
        report = {'kind': 'project-policy-candidate-frontend', 'passed': None,
                  'source_sha256': digest(source.read_bytes()),
                  'scope': 'isolated candidate and headers; not full TU or final ROM'}
        try:
            if os.environ.get('WERROR', '0') != '0':
                raise ValueError('nondefault WERROR environment requires explicit policy')
            recipe = frontend_check.recipe(str(self.repo), (self.repo / 'Makefile').read_text(),
                                           self.task['compile_target'])
            if recipe['makefile_sha256'] != self.recipe['makefile_sha256']:
                raise ValueError('frontend recipe changed during compile')
            converted = work / 'frontend.c'
            _run([sys.executable, str(self.repo / 'tools/textconv.py'), str(self.repo / 'tools/charmap.txt'),
                  str(source), str(converted)], work)
            # Context takes precedence over the live target-local quote path,
            # matching direct compilation's first -I directory.
            command = [recipe['command'][0], '-iquote', str(self.context),
                       '-I' + str(self.context), *recipe['command'][1:], str(converted)]
            process = subprocess.run(command, cwd=self.repo, capture_output=True,
                                     text=True, errors='replace', timeout=60)
            report.update(passed=process.returncode == 0, returncode=process.returncode,
                          recipe=recipe, command=command,
                          diagnostics=(process.stdout + process.stderr)[-16000:])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report['error'] = str(exc)
        write_json(work / 'source.frontend.json', report)
        return report

    def _attribution(self, work, source, wrapped, converted, obj, diff):
        from solver import source_attribution
        try:
            shutil.copyfile(obj, work / 'candidate.source-lines.o')
            (work / 'candidate.source-lines.input.c').write_text(wrapped, encoding='utf-8')
            shutil.copyfile(converted, work / 'candidate.source-lines.c')
            (work / 'candidate.source-lines.path').write_text(str(converted), encoding='utf-8')
            debug = _run(['mips-linux-gnu-objdump', '-drzl', '-m', 'mips:4300', str(obj)], work)
            (work / 'candidate.source-lines.dump').write_text(debug, encoding='utf-8')
            shutil.copyfile(self.repo / 'tools/claude-decomp-env/objdump.py', work / 'objdump.py')
            return source_attribution.collect(work, 'candidate', source, wrapped, diff)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            return {'status': 'unavailable', 'reason': str(exc), 'instructions': []}

    def key(self, source):
        from solver import ido_stages
        self.key_calls += 1
        start = time.monotonic()
        row = {'source_sha256': digest(source.encode()), 'key': None}
        try:
            admit_source(source)
            if compiler_recipe.resolve(self.repo, self.task['compile_target']) != self.recipe:
                raise ValueError('key recipe changed during audit')
            row['key'] = ido_stages.optimizer_key(self.repo, self.key_workspace, self.task['function'], source)
            return row['key']
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            row['seconds'] = time.monotonic() - start
            self.key_seconds += row['seconds']
            with (self.output / 'keys.jsonl').open('a', encoding='utf-8') as out:
                out.write(json.dumps(row) + '\n')

    def record_resolution(self, row):
        with (self.output / 'resolutions.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps(row) + '\n')

    def same_object(self, left, right):
        if left.obj is None or right.obj is None:
            return None
        folder = self.output / 'comparisons'
        folder.mkdir(exist_ok=True)
        a, b = folder / (digest(left.obj) + '.o'), folder / (digest(right.obj) + '.o')
        a.write_bytes(left.obj)
        b.write_bytes(right.obj)
        cert = byte_certificate.certify(a, b, source='')
        write_json(folder / (digest(left.obj) + '-' + digest(right.obj) + '.json'), cert)
        return cert['exact'] if cert['status'] != 'unverified' else None

    def costs(self):
        return {'compiles': self.calls, 'compile_seconds': self.seconds,
                'key_calls': self.key_calls, 'key_seconds': self.key_seconds}
