"""Linux-only execution boundary for untrusted repair tools; never falls back."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

MAX_SOURCE = 16000
MAX_OUTPUT = 192000
MAX_CANDIDATES = 8

# Applied before executing model code. The loaded kernel filter cannot be removed
# by Python introspection or a direct libc syscall. Missing libseccomp fails closed.
BOOTSTRAP = '''import ctypes, errno
code = open('/tool.py', encoding='utf-8').read()
lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
lib.seccomp_init.argtypes = [ctypes.c_uint32]
lib.seccomp_init.restype = ctypes.c_void_p
lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
lib.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
lib.seccomp_load.argtypes = [ctypes.c_void_p]
lib.seccomp_release.argtypes = [ctypes.c_void_p]
ctx = lib.seccomp_init(0x7fff0000)
assert ctx
for name in ('clone', 'clone3', 'fork', 'vfork', 'execve', 'execveat',
             'unshare', 'setns', 'ptrace', 'process_vm_readv', 'process_vm_writev',
             'memfd_create', 'shmget', 'shmat', 'mount', 'bpf', 'perf_event_open',
             'keyctl', 'add_key', 'request_key'):
    call = lib.seccomp_syscall_resolve_name(name.encode())
    if call >= 0:
        assert lib.seccomp_rule_add(ctx, 0x00050000 | errno.EPERM, call, 0) == 0
assert lib.seccomp_load(ctx) == 0
lib.seccomp_release(ctx)
exec(compile(code, '/tool.py', 'exec'), {'__name__': '__main__'})
'''


def namespace_command():
    bwrap = shutil.which('bwrap')
    if sys.platform != 'linux' or not bwrap:
        raise RuntimeError('Linux Bubblewrap is required; unsandboxed execution is disabled')
    return [bwrap, '--unshare-all', '--unshare-user', '--disable-userns', '--die-with-parent', '--new-session',
            '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/bin:/compiler',
            '--setenv', 'TMPDIR', '/tmp', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
            '--size', '16777216', '--tmpfs', '/tmp',
            '--size', '16777216', '--tmpfs', '/work',
            '--size', '1048576', '--tmpfs', '/dev/shm', '--chdir', '/work']


def execute(command, *, input_text='', timeout=3, output_limit=MAX_OUTPUT,
            memory_bytes=384 * 1024**2):
    """Parent-owned bounded files avoid unbounded pipe buffering and descendant hangs."""
    import resource

    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('timeout must be between zero and 60 seconds')

    def limits():
        resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(timeout), math.ceil(timeout) + 1))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (max(output_limit, 4 * 1024**2),) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    start = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                start_new_session=True, preexec_fn=limits, env={})
        status = 'ok'
        try:
            proc.communicate(input_text.encode(), timeout=timeout)
            if proc.returncode:
                status = 'process_error'
        except subprocess.TimeoutExpired:
            status = 'timeout'
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(output_limit + 1), stderr.read(output_limit + 1)
        if len(out) > output_limit or len(err) > output_limit:
            status = 'output_limit'
        return {'status': status, 'returncode': proc.returncode,
                'stdout': out[:output_limit].decode('utf-8', errors='replace'),
                'stderr': err[:output_limit].decode('utf-8', errors='replace'),
                'seconds': time.monotonic() - start}


def run_tool(code: str, observation: dict, *, timeout=3) -> dict:
    start = time.monotonic()
    base = {'candidates': [], 'seconds': 0.0}
    try:
        command = namespace_command()
    except RuntimeError as exc:
        return {**base, 'status': 'unavailable', 'error': str(exc)}
    try:
        if not isinstance(code, str) or not 1 <= len(code.encode()) <= 48000:
            raise ValueError('tool code must be 1..48000 bytes')
        payload = json.dumps(observation, allow_nan=False)
        if len(payload.encode()) > 96000:
            raise ValueError('observation exceeds 96000 bytes')
        with tempfile.TemporaryDirectory(prefix='repair-tool-') as directory:
            script = Path(directory) / 'tool.py'
            script.write_text(code, encoding='utf-8')
            bootstrap = Path(directory) / 'bootstrap.py'
            bootstrap.write_text(BOOTSTRAP, encoding='utf-8')
            row = execute([*command, '--ro-bind', str(script), '/tool.py',
                           '--ro-bind', str(bootstrap), '/bootstrap.py',
                           '--remount-ro', '/', '--remount-ro', '/dev',
                           '/usr/bin/python3', '-I', '-S', '/bootstrap.py'],
                          input_text=payload, timeout=timeout)
        if row['status'] != 'ok':
            return {**row, 'candidates': []}
        value = json.loads(row.pop('stdout'))
        if not isinstance(value, dict) or set(value) != {'candidates'}:
            raise ValueError('tool output must contain only candidates; no verdicts or paths')
        candidates = value['candidates']
        if (not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES
                or any(not isinstance(c, str) or not 1 <= len(c.encode()) <= MAX_SOURCE
                       for c in candidates)):
            raise ValueError('expected at most eight bounded source strings')
        return {**row, 'candidates': list(dict.fromkeys(candidates))}
    except (ValueError, OSError, TypeError, RecursionError) as exc:
        return {**base, 'status': 'invalid', 'error': str(exc),
                'seconds': time.monotonic() - start}
