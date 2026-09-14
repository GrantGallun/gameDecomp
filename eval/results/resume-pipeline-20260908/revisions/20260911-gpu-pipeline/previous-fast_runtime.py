"""Process-local measured reuse under a controller-verified immutable input pin.

Only enabled by the fast controller. No model responses or acceptance verdicts
are fabricated. Compiler cache replays build artifacts; score() still performs
its ordinary frontend and exact-certificate checks on every call.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def install(root, pin, model_lock):
    import fcntl
    from solver import llm, type_constraints, workspace
    from eval import semantic_lane
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    metrics = {'model_queue_seconds':0., 'model_seconds':0., 'model_calls':0,
               'layout_hits':0, 'layout_misses':0, 'layout_seconds':0.,
               'semantic_hits':0, 'semantic_misses':0, 'semantic_seconds':0.,
               'compile_hits':0, 'compile_misses':0, 'compile_seconds':0.}

    @contextmanager
    def entry(kind, identity):
        digest = key([pin, kind, identity])
        directory = root / kind / digest[:2] / digest
        directory.mkdir(parents=True, exist_ok=True)
        with (directory/'lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield directory

    def read(directory):
        path = directory/'value.json'
        if not path.exists():
            return None
        record = json.loads(path.read_bytes())
        if key(record['value']) != record['sha256']:
            raise ValueError('performance cache checksum mismatch')
        return record['value']

    def write(directory, value):
        from eval.campaign_state import atomic
        atomic(directory/'value.json', {'sha256':key(value), 'value':value})

    generate = llm.generate
    def serialized_model(*args, **kwargs):
        start = time.monotonic()
        with Path(model_lock).open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            waited = time.monotonic()-start
            metrics['model_queue_seconds'] += waited
            start = time.monotonic()
            try:
                text, metadata = generate(*args, **kwargs)
                metadata['_campaign_queue_seconds'] = waited
                return text, metadata
            finally:
                metrics['model_calls'] += 1
                metrics['model_seconds'] += time.monotonic()-start
    llm.generate = serialized_model

    measure = type_constraints.measure
    def measured(repo, ws, source, function, target, **kwargs):
        start = time.monotonic()
        from solver import compile_obligations, compiler_recipe
        includes = '\n'.join(re.findall(r'(?m)^\s*#\s*include[^\n]+',source))
        provided = compile_obligations.header_types(repo,source,function)
        identity = [str(repo), includes, provided, target, kwargs]
        with entry('layouts', identity) as directory:
            result = read(directory)
            if result is not None:
                metrics['layout_hits'] += 1
            else:
                metrics['layout_misses'] += 1
                result = measure(repo, ws, source, function, target, **kwargs)
                result = {k:v for k,v in result.items() if k != 'receipt_path'}
                write(directory, result)
        result['source_sha256'] = compiler_recipe.sha(source.encode())
        path = ws/('type-constraints-layout-'+compiler_recipe.sha(json.dumps(result,sort_keys=True).encode())+'.json')
        path.write_text(json.dumps(result,indent=2)+'\n')
        result['receipt_path'] = str(path)
        metrics['layout_seconds'] += time.monotonic()-start
        return copy.deepcopy(result)
    type_constraints.measure = measured

    evaluate = semantic_lane.Panel.__call__
    def semantic(panel, state):
        if not state.attempt.compiled or (state.attempt.frontend or {}).get('passed') is not True:
            return evaluate(panel, state)
        start = time.monotonic()
        obj = state.object_path
        assembly = obj.with_name(obj.stem+'_object_dump_normalized.s')
        identity = [panel.identity, state.source, hashlib.sha256(obj.read_bytes()).hexdigest(),
                    hashlib.sha256(assembly.read_bytes()).hexdigest()]
        with entry('semantic', identity) as directory:
            result = read(directory)
            if result is not None:
                metrics['semantic_hits'] += 1
            else:
                metrics['semantic_misses'] += 1
                result = evaluate(panel, state)
                if result is not None:
                    write(directory, result)
        metrics['semantic_seconds'] += time.monotonic()-start
        return copy.deepcopy(result)
    semantic_lane.Panel.__call__ = semantic

    shell = workspace.sh
    def build(cmd, cwd=None, timeout=300):
        # Only the exact build invocation made by workspace.score is eligible.
        match = re.fullmatch(r'\. (.+) && bash (.+) (\S+\.c)', cmd)
        if not match or cwd is None:
            return shell(cmd, cwd=cwd, timeout=timeout)
        try:
            script = Path(shlex.split(match[2])[0])
            source = Path(cwd)/shlex.split(match[3])[0]
            if not source.is_file() or not script.is_file():
                return shell(cmd, cwd=cwd, timeout=timeout)
            files = [script, source, *Path(cwd).glob('target*'), *Path(cwd).glob('.compiler-*')]
            if (Path(cwd)/'prelude.inc').exists():
                files.append(Path(cwd)/'prelude.inc')
            identity = [cmd, str(cwd), [(str(p), hashlib.sha256(p.read_bytes()).hexdigest())
                                       for p in sorted(set(files)) if p.is_file()]]
        except (ValueError, OSError):
            return shell(cmd, cwd=cwd, timeout=timeout)
        start = time.monotonic()
        with entry('compile', identity) as directory:
            result = read(directory)
            if result is not None:
                metrics['compile_hits'] += 1
                for name, payload in result['artifacts'].items():
                    if Path(name).name != name or not name.startswith(source.stem):
                        raise ValueError('invalid cached build artifact')
                    (Path(cwd)/name).write_bytes(bytes.fromhex(payload))
                answer = (result['returncode'], result['stdout'])
            else:
                metrics['compile_misses'] += 1
                answer = shell(cmd, cwd=cwd, timeout=timeout)
                # Failed builds are not cached: incomplete outputs are ambiguous.
                if answer[0] == 0:
                    artifacts = {p.name:p.read_bytes().hex() for p in Path(cwd).glob(source.stem+'*')
                                 if p.is_file() and p != source and p.suffix != '.json'}
                    write(directory, {'returncode':answer[0], 'stdout':answer[1], 'artifacts':artifacts})
        metrics['compile_seconds'] += time.monotonic()-start
        return answer
    workspace.sh = build
    return metrics
