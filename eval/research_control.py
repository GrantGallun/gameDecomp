"""Dashboard bridge to one independently supervised WSL research worker."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from eval.local_research import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]
MODELS = ('qwen2.5-coder:14b', 'qwen3:8b', 'gpt-oss:20b')
ACTIVE = {'starting', 'running', 'stopping'}


class ResearchControl:
    def __init__(self, directory, distro='Ubuntu', python='/home/grant/decomp/sbk1/.venv/bin/python',
                 *, enabled=True, state='/home/grant/decomp/local-research', repo='/home/grant/decomp/sbk1'):
        self.directory = Path(directory)
        self.distro, self.python, self.enabled = distro, python, enabled
        self.state, self.repo = state, repo
        self.lock = threading.Lock()
        self.process = self.pending = self.log = None

    def get(self):
        value = read_json(self.directory/'status.json', {}) or {}
        value = dict(value)
        if (self.process and self.pending and self.process.poll() is not None
                and value.get('run_id') != self.pending['run_id'] and value.get('status') in ACTIVE
                and time.time()-value.get('updated_at', 0) <= 20):
            # A rejected duplicate launch must not mask the original worker.
            self.process = self.pending = None
        if self.process and self.pending:
            exit_code = self.process.poll()
            if value.get('run_id') != self.pending['run_id']:
                value = dict(self.pending)
            if exit_code is not None and value.get('status') in ACTIVE:
                value.update(status='failed', phase='Worker exited before finishing')
                if self.log and self.log.exists():
                    with self.log.open('rb') as stream:
                        stream.seek(max(0, self.log.stat().st_size-2000))
                        value['last_error'] = stream.read().decode('utf-8', errors='replace')
        if value.get('status') in ACTIVE and time.time()-value.get('updated_at', 0) > 20:
            value.update(status='interrupted', phase='Worker heartbeat lost; it may have exited')
        value.setdefault('status', 'off')
        value.setdefault('phase', 'Ready for local compiler research')
        value['active'] = value['status'] in ACTIVE
        stop = read_json(self.directory/'stop.json', {}) or {}
        if value['active'] and stop.get('run_id') == value.get('run_id'):
            value.update(status='stopping', phase='Stopping the current tool call')
        value.update(controls_enabled=self.enabled, models=list(MODELS), api_spend_usd=0)
        return value

    def __call__(self, action, options):
        if not self.enabled:
            raise ValueError('Research controls are disabled in read-only mode')
        if action not in ('start', 'stop') or not isinstance(options, dict):
            raise ValueError('Research action must be start or stop')
        with self.lock:
            current = self.get()
            if action == 'stop':
                if options:
                    raise ValueError('Stop accepts no options')
                if current.get('run_id'):
                    atomic_json(self.directory/'stop.json', {'run_id': current['run_id']})
                return self.get()
            if set(options) - {'minutes', 'max_calls', 'model'}:
                raise ValueError('Unknown research option')
            minutes, calls = options.get('minutes', 30), options.get('max_calls', 12)
            model = options.get('model', MODELS[0])
            if type(minutes) is not int or not 1 <= minutes <= 120:
                raise ValueError('Minutes must be an integer from 1 to 120')
            if type(calls) is not int or not 1 <= calls <= 48:
                raise ValueError('Model calls must be an integer from 1 to 48')
            if model not in MODELS:
                raise ValueError('Select an installed local model from the list')
            if current['active'] or (self.process and self.process.poll() is None):
                raise RuntimeError('Local research is already running')
            from eval.progress_app import wsl_path
            self.directory.mkdir(parents=True, exist_ok=True)
            run_id = uuid.uuid4().hex
            args = [self.python, '-m', 'eval.local_research', '--state', self.state, '--repo', self.repo,
                    '--view', wsl_path((self.directory/'status.json').resolve()),
                    '--stop', wsl_path((self.directory/'stop.json').resolve()),
                    '--run-id', run_id, '--minutes', str(minutes), '--max-calls', str(calls), '--model', model]
            command = (['wsl.exe', '-d', self.distro, '--cd', wsl_path(ROOT), '-e'] + args
                       if os.name == 'nt' else args)
            self.log = self.directory/f'worker-{run_id}.log'
            with self.log.open('wb') as output:
                self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    start_new_session=os.name != 'nt')
            self.pending = {'run_id': run_id, 'status': 'starting', 'phase': 'Starting WSL worker',
                            'updated_at': time.time(), 'minutes': minutes, 'max_calls': calls,
                            'max_compiles': calls*6, 'model': model}
            return self.get()
