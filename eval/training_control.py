"""Dashboard bridge and process-group supervisor for one local adapter-training run.

Training is a SEPARATE, EXPLICIT action beside the compiler-research action. It is
not a stage of the research loop, it does not edit the notebook, and nothing here
decides whether an adapter is good. This module only starts a bounded run, reports
what the run actually did, and can stop it.

Two roles in one file, mirroring `eval/local_research.py` (worker) and
`eval/research_control.py` (dashboard bridge):

* `TrainingControl` -- the dashboard side. It launches one worker under WSL, projects
  the worker's status file for the panel, and stops the run by writing the shared
  stop file and then killing the WSL process GROUP that holds the trainer and every
  compiler subprocess it spawned.
* `main()` with `--worker` -- the WSL side. It spawns the trainer
  (`python -m eval.train_repair_sft`) in its own session so the trainer's children
  share a killable process group, heartbeat-publishes the run state, applies the
  wall-clock limit itself, captures the trainer's own step lines as stage progress,
  and writes the terminal receipt.

WHERE THE STATE LIVES (all on the WSL filesystem except the projection):

    /home/grant/decomp/local-research/training/runs/<run-id>/
        events.jsonl   every stage change and every trainer step/limit line
        trainer.log    the trainer's stdout+stderr, line for line
        receipt.json   the terminal record: status, counters, terminal reason
    <projection>/training-status.json   heartbeat view the panel polls
    <projection>/training-receipt.json  copy of the terminal receipt
    <projection>/training-stop.json     stop request, keyed by run id
    <projection>/training.pid           {run_id, pgid} of the live worker

THE STATE MACHINE. The panel shows exactly one of:

    off          no run has been recorded
    starting     a worker was launched; nothing published yet
    running      the worker's status file is fresh and the run is live
    stopping     a stop was requested for this run id and the worker is still up
    completed    the trainer exited 0 AND its receipt was verified; this is the
                 ONLY state in which `adapter_published` can be true
    stopped      the stop file fired, or a configured limit cut the run short
    failed       non-zero exit, or a clean exit whose receipt was missing/invalid
    interrupted  the worker stopped publishing (heartbeat stale): crash, kill, or a
                 shutdown before it could write a receipt

`stopped`, `interrupted` and `failed` never publish an adapter, and neither does
`completed` unless the trainer's own receipt confirms one. This module says what
happened; it makes no claim that training improved anything. Evaluation against
held-out tasks is a separate, privileged step.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from eval.local_research import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]

# --- launch contract -----------------------------------------------------------
# `eval.train_source_repair` is the compiler-verified post-training trainer: it is
# the stage that actually trains and publishes an adapter. This is the entry point
# and the flags the control plane knows how to supply. A flag the trainer does not
# advertise is omitted rather than passed blindly, and the omission is recorded.
TRAINER_MODULE = 'eval.train_source_repair'
TRAINER_FLAGS = ('--base', '--tasks', '--out', '--split', '--max-seq-len', '--max-steps',
                 '--max-seconds', '--max-examples', '--block-size')
# A limit the trainer cannot enforce cannot be honoured, and quietly running without
# it would make the panel's declared limits false. These are refused up front.
REQUIRED_TRAINER_FLAGS = ('--base', '--tasks', '--out', '--max-steps', '--max-examples',
                          '--max-seconds')
# The trainer's own publish marker and receipt, relative to its `--out` directory.
PUBLISH_MARKER = 'PUBLISHED.json'
TRAINER_RECEIPT = 'training_receipt.json'


def trainer_workdir():
    """The directory the trainer must run in: THIS CHECKOUT, where `eval` is importable.

    Not the game repo. The working directory used to default to `~/decomp/sbk1` and was then
    handed to both the flag probe and the trainer launch, but the trainer is invoked as
    `python -m eval.train_source_repair` and the `eval` package lives in this checkout, which
    the game repo does not contain. So the probe asked an interpreter to import a module it
    could not reach and reported the failure as a trainer defect, and a launch would have
    failed the same way -- the dashboard's training action could never have started a run.

    Measured, not assumed: with cwd = the game repo the venv python exits
    `ModuleNotFoundError: No module named 'eval'`; with cwd = this checkout it imports. There is
    no `PYTHONPATH` and no `.pth` that would rescue the game repo, and the trainer takes no
    `--repo` at all -- its task file already carries the compiled, certified records -- so
    nothing on this path needs the game repo.

    Returned in the spelling WSL needs, because the worker runs there.
    """
    from eval.progress_app import wsl_path
    return wsl_path(ROOT)

# --- defaults and hard ceilings ------------------------------------------------
# The base weights and default task file are this installation's real paths; both are
# overridable per request, and a run refuses to start without a base.
DEFAULT_BASE = '/home/grant/decomp/models/qwen2.5-coder-7b'
# The interpreter that actually has torch/peft installed here. The bare name `python`
# does NOT exist in this WSL install, so it is never a candidate.
DEFAULT_TRAINER_PYTHON = '/home/grant/decomp/train-venv/bin/python'
DEFAULT_TASKS = 'tasks.jsonl'
DEFAULT_SEQ_LEN, MAX_SEQ_LEN = 2304, 16384
DEFAULT_BLOCK_SIZE, MAX_BLOCK_SIZE = 4, 64
DEFAULT_MINUTES, MAX_MINUTES = 60, 480
DEFAULT_STEPS, MAX_STEPS = 600, 20000
DEFAULT_EXAMPLES, MAX_EXAMPLES = 20000, 200000

ACTIVE = {'starting', 'running', 'stopping'}
# Terminal states. `already_published` exists so the trainer's deliberate refusal to
# overwrite a published adapter is never mistaken for a retryable failure.
TERMINAL = {'off', 'completed', 'stopped', 'failed', 'interrupted', 'already_published'}
STALE_SECONDS = 20          # matches the research panel's heartbeat window
PUBLISH_SECONDS = 1.0       # mirror of local_research.Research.publish
KILL_GRACE_SECONDS = 10.0   # SIGTERM the group, then SIGKILL if it is still up
_RUN_ID = re.compile(r'[a-zA-Z0-9-]{1,64}')
_OPAQUE = re.compile(r'^[^\x00-\x1f\x7f]{1,512}$')     # no control chars, no newlines


def _flag(text):
    """An opaque launch argument: no control characters, no line breaks."""
    return isinstance(text, str) and bool(_OPAQUE.match(text))


def _count(name, value, ceiling):
    if type(value) is not int or not 1 <= value <= ceiling:
        raise ValueError(f'{name} must be an integer from 1 to {ceiling}')
    return value


def group_kill_command(distro, pgid, signal_name, *, command='kill', cd=None, python=None):
    """The process-group kill as an argv (never a shell string).

    `kill -TERM -<pgid>`: the negative operand is the whole group, so the trainer and
    every compiler it forked receive the signal, not only its direct child. It runs
    through `wsl.exe -e`, because a Windows-side kill of the launcher does not reach
    processes inside the WSL session.
    """
    prefix = [command, '-d', distro] + (['--cd', cd] if cd else []) + ['-e']
    return prefix + [python, '-m', 'eval.training_control', '--signal', signal_name,
                     '--pgid', str(int(pgid))]


def terminate_group(pgid, *, grace=KILL_GRACE_SECONDS, alive=None, send=None):
    """SIGTERM the group, escalate to SIGKILL, and never raise when it is already gone.

    Idempotent and safe with nothing running: signalling a group that no longer
    exists is a success, not an error (`kill` exits 1 when the group is gone, so its
    exit status is ignored and only liveness decides whether to escalate).
    """
    alive = alive or (lambda: group_alive(pgid))
    send = send or (lambda name: _default_send(pgid, name))
    if not alive():
        return []
    # Liveness is re-probed through the callback so a caller can watch a real
    # process table; the default callback does not fork, so this stays cheap.
    sent = ['TERM']
    send('TERM')
    end = time.monotonic() + grace
    while alive() and time.monotonic() < end:
        time.sleep(0.05)
    if alive():
        sent.append('KILL')
        send('KILL')
        end = time.monotonic() + 2.0
        while alive() and time.monotonic() < end:
            time.sleep(0.05)
    return sent


def _default_send(pgid, name):
    try:
        os.killpg(int(pgid), signal.SIGTERM if name == 'TERM' else signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def group_alive(pgid):
    """True while any process still belongs to that group (leader exit is not enough)."""
    try:
        os.killpg(int(pgid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        # This host has no POSIX process groups (Windows). The worker's own pid is
        # the only identity available, so liveness is asked of the process table.
        return pid_alive(pgid)
    return True


def pid_alive(pid):
    """Process-table liveness, so a non-POSIX host does not assume a group is gone.

    On Windows `os.kill(pid, 0)` is not a liveness probe: CPython routes it to
    `TerminateProcess`, which is destructive and does not report absence. Asking
    the process table is the only safe check there.
    """
    if os.name == 'nt':
        done = subprocess.run(['tasklist', '/FI', f'PID eq {int(pid)}', '/NH', '/FO', 'CSV'],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return f'"{int(pid)}"' in (done.stdout or '')
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _signal_group(pgid, name):
    """`--signal` entry point. Also the fallback if the dashboard runs inside WSL."""
    signo = signal.SIGTERM if name == 'TERM' else signal.SIGKILL
    try:
        os.killpg(int(pgid), signo)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        print(json.dumps({'signalled': False, 'pgid': int(pgid), 'signal': name,
                          'note': str(exc)}))
        return 0                      # an already-dead group is the desired end state
    print(json.dumps({'signalled': True, 'pgid': int(pgid), 'signal': name}))
    return 0


class TrainingControl:
    """Dashboard side of one training run. Same conventions as ResearchControl."""

    def __init__(self, directory, distro='Ubuntu', python='/home/grant/decomp/train-venv/bin/python',
                 *, enabled=True, state='/home/grant/decomp/local-research',
                 repo=None, wsl='wsl.exe', launch=True, log=None,
                 trainer_module=TRAINER_MODULE, interpreter_fallbacks=()):
        self.directory = Path(directory)
        self.distro, self.python, self.enabled = distro, python, enabled
        # `repo` is the trainer's working directory, i.e. the import root for
        # `python -m <trainer_module>` -- see `trainer_workdir`. It is not the game repo.
        self.state, self.repo = state, (repo if repo is not None else trainer_workdir())
        self.wsl = wsl
        self.launch = launch
        self.log = log
        self.trainer_module = trainer_module
        # Off by default: the run uses the interpreter it was configured with, and a
        # missing one is reported rather than silently swapped for another environment.
        self.interpreter_fallbacks = tuple(interpreter_fallbacks)
        self.lock = threading.Lock()
        self.process = None

    # --- paths -----------------------------------------------------------------
    @property
    def runs(self):
        return self.state.rstrip('/') + '/training/runs'

    def run_paths(self, run_id):
        return f'{self.runs}/{run_id}'

    # --- projection ------------------------------------------------------------
    def get(self):
        value = dict(read_json(self.directory/'training-status.json', {}) or {})
        record = read_json(self.directory/'training-receipt.json', None)
        declared = record if isinstance(record, dict) and record.get('run_id') else None
        # Two record shapes reach this file: the worker's own run record and, when no
        # worker survived, the trainer's receipt. Both mark publication, and only the
        # first one describes a worker that verified the marker itself.
        declares_publication = bool(declared) and bool(declared.get('adapter_published')
                                                       or declared.get('published'))
        exit_code = self.process.poll() if self.live_process() else None
        if declared and not value.get('run_id'):
            value.update(declared)          # the receipt is the record when nothing else is
        elif declared and declared.get('run_id') != value.get('run_id'):
            # A receipt for a different run may never describe this one, and it may
            # never authorize an adapter for this one.
            declared = None
            declares_publication = False
        stale = (value.get('status') in ACTIVE
                 and time.time()-value.get('updated_at', 0) > STALE_SECONDS)
        if stale:
            # The worker stopped publishing: a crash, an external kill, or a shutdown
            # that never reached its receipt. The run is neither completed nor counted.
            value.update(status='interrupted',
                         phase='Training worker stopped publishing; no receipt was written')
        elif value.get('status') in ACTIVE and exit_code is not None and not declared:
            value.update(status='failed', phase='Training worker exited before writing a receipt')
        value.setdefault('status', 'off')
        value.setdefault('phase', 'Ready to start a bounded local training run')
        if value['status'] == 'completed' and not declared:
            # Nothing may claim a completed run without the receipt that proves it.
            value.update(status='failed',
                         phase='A completed run was claimed without a written receipt')
        if value['status'] not in ACTIVE | TERMINAL:
            value.update(status='failed',
                         terminal_reason='unknown status in the status projection')
        status = value['status']
        stop = read_json(self.directory/'training-stop.json', {}) or {}
        stop_pending = bool(value.get('run_id')) and stop.get('run_id') == value.get('run_id')
        if status in ACTIVE and stop_pending:
            value.update(status='stopping', phase='Stopping the training process group')
            status = 'stopping'
        value['active'] = status in ACTIVE
        value['stop_pending'] = stop_pending and value['active']
        # An adapter is published only when the record for THIS run says so. A stopped,
        # interrupted, failed or no-op run publishes nothing, whatever files it left.
        published = None
        if status == 'completed' and declares_publication:
            adapter = declared.get('published_marker') or declared.get('adapter') or \
                declared.get('adapter_out') or ''
            if isinstance(adapter, str) and adapter:
                published = str(Path(adapter).parent if adapter.endswith('.json') else adapter)
        value['published_adapter'] = published
        value['adapter_published'] = bool(published)
        change = declared.get('weight_change') if declared else None
        if isinstance(change, dict):
            value['tensors_changed'] = change.get('tensors_changed')
        value.setdefault('steps', 0)
        value.setdefault('examples_used', 0)
        value.setdefault('terminal_reason', None)
        value.setdefault('stop_reason', None)
        value.setdefault('peak_gpu_gb', None)
        value['controls_enabled'] = self.enabled
        value['trainer'] = TRAINER_MODULE
        value['limits'] = {'defaults': {'max_steps': DEFAULT_STEPS, 'minutes': DEFAULT_MINUTES,
                                        'max_examples': DEFAULT_EXAMPLES,
                                        'max_seq_len': DEFAULT_SEQ_LEN,
                                        'block_size': DEFAULT_BLOCK_SIZE},
                           'ceilings': {'max_steps': MAX_STEPS, 'minutes': MAX_MINUTES,
                                        'max_examples': MAX_EXAMPLES,
                                        'max_seq_len': MAX_SEQ_LEN,
                                        'block_size': MAX_BLOCK_SIZE}}
        return value

    def live_process(self):
        return self.process is not None and bool(
            getattr(self.process, 'pending', False) or self.process.poll() is None)

    # --- control ---------------------------------------------------------------
    def __call__(self, action, options):
        if not self.enabled:
            raise ValueError('Training controls are disabled in read-only mode')
        if action not in ('start', 'stop') or not isinstance(options, dict):
            raise ValueError('Training action must be start or stop')
        with self.lock:
            current = self.get()
            if action == 'stop':
                return self.stop(current, options)
            return self.start(current, options)

    def stop(self, current=None, options=None):
        """Idempotent. Writes the shared stop file, then kills the worker's group."""
        current = self.get() if current is None else current
        if options:
            raise ValueError('Stop accepts no options')
        run_id = current.get('run_id')
        if not run_id:
            return self.get()                       # nothing has ever run: a no-op
        if not current.get('active'):
            return self.get()                       # already terminal: still a no-op
        atomic_json(self.directory/'training-stop.json', {'run_id': run_id})
        pid_file = read_json(self.directory/'training.pid', {}) or {}
        if pid_file.get('run_id') != run_id or not pid_file.get('pgid'):
            return self.get()                       # no live worker group to signal
        pgid = int(pid_file['pgid'])
        try:
            sent = terminate_group(pgid, alive=lambda: self.group_alive(pgid),
                                   send=lambda name: self.send_group_signal(pgid, name))
            self.receipt_note(run_id, {'stop_signals': sent})
            if sent:
                self.mark_stopped(run_id, 'stopped by the user',
                                  f'the process-group {sent[-1]} signal was delivered')
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            self.receipt_note(run_id, {'stop_error': str(exc)[-500:]})
        return self.get()

    def mark_stopped(self, run_id, reason, phase):
        """Record a verified stop in the projection when no worker is left to do it.

        The worker owns the receipt, but a Stop delivered straight to its process
        group can end it before it writes one. Leaving the panel on `stopping` would
        hide a finished cancellation, so the projection is closed out here -- with
        `adapter_published` false and no receipt claiming otherwise.
        """
        path = self.directory/'training-status.json'
        value = read_json(path, {}) or {}
        if value.get('run_id') != run_id or value.get('status') not in ACTIVE:
            return
        value.update(status='stopped', phase=phase, active=False,
                     terminal_reason=reason, adapter_published=False,
                     updated_at=time.time())
        atomic_json(path, value)

    def group_alive(self, pgid):
        """Liveness of the supervised group, measured without joining it.

        From Windows a process inside the WSL session is not visible to `os.killpg`.
        A Python helper that asks `kill -0 -<pgid>` would itself become a member of
        the group it is measuring, and would then report that group alive forever --
        so the question goes to `kill` itself, which is part of no group but its own.
        """
        if os.name != 'nt':
            return group_alive(pgid)
        # The question is put to the shell: `wsl.exe -e kill -0 -<pgid>` treats the
        # negative operand as its own option, while `sh -c 'kill -0 -<pgid>'` gets the
        # POSIX "is any process in this group" test, which exits 0 only while one is.
        argv = [self.wsl, '-d', self.distro, '-e', 'sh', '-c', f'kill -0 -{int(pgid)}']
        with self.log_stream() as stream:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=15,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                  ).returncode == 0

    def probe(self, *, argv, timeout=15):
        with self.log_stream() as stream:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stream,
                                  stderr=subprocess.STDOUT, timeout=timeout,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                                  ).returncode == 0

    def send_group_signal(self, pgid, name):
        """`kill -<sig> -<pgid>` inside WSL reaches the trainer and its compilers."""
        if os.name != 'nt':
            _signal_group(pgid, name)
            return
        argv = group_kill_command(self.distro, pgid, name,
                                  cd=ROOT.as_posix(), python=self.python)
        argv[0] = self.wsl
        if not self.probe(argv=argv):
            raise RuntimeError(f'the process-group {name} signal reported failure')

    def log_stream(self):
        if self.log is None:
            self.log = self.directory/'training-control.log'
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.log.open('ab')

    def receipt_note(self, run_id, fields):
        path = self.directory/'training-receipt.json'
        value = read_json(path, {}) or {}
        if value.get('run_id') == run_id:
            value.update(fields)
            atomic_json(path, value)

    def start(self, current=None, options=None):
        current = self.get() if current is None else current
        options = options or {}
        if set(options) - {'minutes', 'max_steps', 'max_examples', 'tasks', 'dataset', 'out',
                           'base', 'split', 'max_seq_len', 'block_size'}:
            raise ValueError('Unknown training option')
        minutes = _count('minutes', options.get('minutes', DEFAULT_MINUTES), MAX_MINUTES)
        steps = _count('max_steps', options.get('max_steps', DEFAULT_STEPS), MAX_STEPS)
        examples = _count('max_examples', options.get('max_examples', DEFAULT_EXAMPLES),
                          MAX_EXAMPLES)
        seq_len = _count('max_seq_len', options.get('max_seq_len', DEFAULT_SEQ_LEN), MAX_SEQ_LEN)
        block = _count('block_size', options.get('block_size', DEFAULT_BLOCK_SIZE),
                       MAX_BLOCK_SIZE)
        # `--tasks` is the trainer's flag; `dataset` is accepted as the caller's name for
        # the same file, so the two vocabularies cannot silently disagree.
        tasks = options.get('tasks', options.get('dataset',
                                                 f'{self.state}/training/{DEFAULT_TASKS}'))
        out = options.get('out', f'{self.state}/training/adapters')
        base = options.get('base', DEFAULT_BASE)
        split = options.get('split', 'train')
        for name, value in [('tasks', tasks), ('out', out), ('base', base), ('split', split)]:
            if value is not None and not _flag(value):
                raise ValueError(f'{name} must be a plain path or name without control characters')
        if not base:
            raise ValueError('base must name the model weights the adapter is trained from')
        # The trainer runs under WSL, so a Windows-side path would resolve to a file
        # that does not exist there. A drive-letter path is the mistake this catches.
        for name, value in [('tasks', tasks), ('out', out), ('base', base)]:
            if re.match(r'^[A-Za-z]:[\\/]', value):
                raise ValueError(f'{name} must be a WSL path, not the Windows path {value!r}; '
                                 f'for example {self.state}/training/...')
        if current.get('active') or self.live_process():
            raise RuntimeError('Local training is already running')
        self.directory.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        out_dir = f'{out}/{run_id}'
        # The launch contract: `python -m eval.train_source_repair` with the documented
        # flags. The receipt and the publish marker live in the trainer's own --out.
        trainer = [self.python, '-m', self.trainer_module, '--base', base, '--tasks', tasks,
                   '--split', split, '--out', out_dir,
                   '--max-seq-len', str(seq_len), '--max-steps', str(steps),
                   '--max-seconds', str(minutes*60), '--max-examples', str(examples),
                   '--block-size', str(block)]
        worker = [self.python, '-m', 'eval.training_control', '--worker',
                  '--state', self.state, '--view',
                  self._wsl((self.directory/'training-status.json').resolve()),
                  '--stop', self._wsl((self.directory/'training-stop.json').resolve()),
                  '--pid', self._wsl((self.directory/'training.pid').resolve()),
                  '--run-id', run_id, '--repo', self.repo,
                  '--trainer-python', self.python, '--max-seconds', str(minutes*60)]
        for fallback in self.interpreter_fallbacks:
            worker += ['--interpreter-fallback', fallback]
        worker += ['--trainer'] + trainer
        if not self.launch:
            return {'run_id': run_id, 'worker': worker, 'trainer': trainer, 'out': out_dir,
                    'receipt': f'{out_dir}/{TRAINER_RECEIPT}',
                    'marker': f'{out_dir}/{PUBLISH_MARKER}'}
        command = ([self.wsl, '-d', self.distro, '--cd', ROOT.as_posix(), '-e'] + worker
                   if os.name == 'nt' else worker)
        self.log = self.directory/f'training-worker-{run_id}.log'
        with self.log.open('wb') as output:
            self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                start_new_session=os.name != 'nt')
        self.process.pending = True
        atomic_json(self.directory/'training-status.json', {
            'run_id': run_id, 'status': 'starting', 'phase': 'Starting the WSL training worker',
            'updated_at': time.time(), 'minutes': minutes, 'max_seconds': minutes*60,
            'max_steps': steps, 'max_examples': examples, 'max_seq_len': seq_len,
            'block_size': block, 'steps': 0, 'examples_used': 0, 'base': base, 'tasks': tasks,
            'adapter_out': out_dir, 'receipt': f'{out_dir}/{TRAINER_RECEIPT}',
            'publish_marker': f'{out_dir}/{PUBLISH_MARKER}',
            'trainer_module': self.trainer_module})
        return self.get()

    @staticmethod
    def _wsl(path):
        from eval.progress_app import wsl_path
        return wsl_path(path)


# ---------------------------------------------------------------------------
# WSL worker
# ---------------------------------------------------------------------------
class Probe:
    """The result of asking a trainer which flags it offers.

    "The interpreter could not be run" and "the trainer offers none of these flags"
    are different findings, and collapsing them is exactly the project's silent-decline
    failure mode: a missing `python` would be reported as a trainer that supports
    nothing. The two cases therefore carry different fields, and the terminal reason
    says which one happened.
    """

    __slots__ = ('interpreter', 'flags', 'error', 'tried')

    def __init__(self, interpreter=None, flags=None, error=None, tried=()):
        self.interpreter = interpreter
        self.flags = None if flags is None else set(flags)
        self.error = error
        self.tried = list(tried)

    @property
    def ok(self):
        return self.error is None

    @property
    def missing(self):
        if not self.ok:
            return list(REQUIRED_TRAINER_FLAGS)
        return [flag for flag in REQUIRED_TRAINER_FLAGS if flag not in self.flags]

    def as_dict(self):
        return {'interpreter': self.interpreter, 'flags': sorted(self.flags or ()),
                'error': self.error, 'tried': self.tried}


def interpreter_candidates(configured, *, fallbacks=()):
    """The interpreters to try, in order, without duplicates.

    The configured interpreter is the only default: silently training with different
    weights or a different environment than the one asked for would be worse than
    refusing. `fallbacks` is opt-in, and a fallback that runs is always reported.
    """
    ordered = [configured, *fallbacks]
    seen, candidates = set(), []
    for candidate in ordered:
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def probe_trainer(python, repo, module=TRAINER_MODULE, timeout=60):
    """Ask one interpreter which of the known flags the trainer offers.

    A failure to execute the interpreter is reported as an interpreter error, never
    as an empty flag set. `FileNotFoundError` from `subprocess.run` is the common
    case: in this environment `python` does not exist, only `python3` and the
    training venv do.
    """
    target = (['-m', module] if module.startswith('eval.') else [str(module)])
    try:
        done = subprocess.run([python, *target, '--help'], cwd=repo,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, start_new_session=os.name != 'nt')
    except FileNotFoundError as exc:
        return Probe(python, error=f'the interpreter {python!r} could not be executed: {exc}')
    except (OSError, subprocess.SubprocessError) as exc:
        return Probe(python, error=f'the trainer could not be probed with {python!r}: {exc}')
    text = (done.stdout or '') + (done.stderr or '')
    if done.returncode != 0 and not text.strip():
        return Probe(python, error=f'{python} -m {module} --help exited {done.returncode} with '
                                   f'no output')
    # Whole `--flag` tokens, not substrings: argparse wraps its help to the terminal
    # width, so a flag name can be split across two lines.
    offered = set(re.findall(r'(?<![\w-])--[a-z][a-z0-9-]*', text))
    flags = {flag for flag in TRAINER_FLAGS if flag in offered}
    if done.returncode != 0 and not flags:
        return Probe(python, error=f'{python} -m {module} --help exited {done.returncode}: '
                                   + text.strip()[-300:])
    return Probe(python, flags=flags)


def module_of(argv):
    """The trainer target named by a trainer argv: a module for `-m`, else a path to a script.

    `probe_trainer` accepts either an `eval.*` module name or a path to a script, so this returns
    whichever the argv actually names, and never None: a None would be stringified into a file
    called `None` and reported as a trainer defect.

    Reading index 1 unconditionally is what produced `/usr/bin/python3 -m -m --help`. The launch
    argv this module builds is `['<python>', '-m', 'eval.train_source_repair', ...]`, so `-m` sits
    at index 1 and the module at index 2; index 1 put the FLAG where the module belongs. The
    command exited 1 with "No module named --help" and was reported as a trainer that offers no
    flags -- the silent decline again, this time hiding a bug in this line.
    """
    argv = [str(token) for token in argv]
    if '-m' in argv:
        index = argv.index('-m')
        return argv[index + 1] if index + 1 < len(argv) else TRAINER_MODULE
    # No `-m`: the trainer is a script path. Skip flags, and skip the interpreter, which is also
    # a bare token but names no Python source.
    for token in argv:
        if token.startswith('-'):
            continue
        if token.startswith('eval.') or token.endswith('.py'):
            return token
    return TRAINER_MODULE


def interpreter_of(argv, configured=None):
    """The interpreter the trainer argv starts with, or None when it does not name one.

    `['<python>', '-m', ...]` names one; `['-m', ...]` does not, and handing `-m` to
    `trainer_flags` as the interpreter is the same class of bug as `-m -m`.
    """
    if configured:
        return configured
    first = str(argv[0]) if argv else ''
    return first if first and not first.startswith('-') else None


def trainer_flags(python, repo, module=TRAINER_MODULE, timeout=60, fallbacks=()):
    """Probe the configured interpreter, then any opt-in fallbacks.

    Returns `(probe, interpreter)` where `interpreter` ran at all, so the launch
    command uses an interpreter that exists instead of failing the same way at exec
    time. With no fallbacks the configured interpreter is the only candidate, and a
    failure is reported as an interpreter error rather than as missing flags.
    """
    order = interpreter_candidates(python, fallbacks=fallbacks)
    tried, last = [], None
    for candidate in order:
        tried.append(candidate)
        last = probe_trainer(candidate, repo, module=module, timeout=timeout)
        if last.ok:
            last.tried = tried
            return last, candidate
    last = last or Probe(python, error='no interpreter candidates were supplied')
    last.tried = tried
    return last, None


def read_examples(tasks):
    """How many task records the run could draw on, from the tasks file itself."""
    if not tasks:
        return None
    path = Path(tasks)
    if path.is_dir():
        path = path/'tasks.jsonl'
    if not path.is_file():
        return None
    try:
        with path.open(encoding='utf-8') as stream:
            return sum(1 for line in stream if line.strip())
    except OSError:
        return None


class TrainingInterrupted(Exception):
    """The supervisor itself was signalled (another process killed its group)."""


class Training:
    """One bounded trainer run, supervised. Owns the receipt decision."""

    def __init__(self, *, state, view, stop, pid, run_id, trainer, repo, trainer_python=None,
                 max_seconds, limits, probe=None):
        self.state, self.view, self.stop = Path(state), Path(view), Path(stop)
        self.pid, self.run_id, self.max_seconds = Path(pid), run_id, max_seconds
        self.trainer, self.repo = list(trainer), Path(repo)
        self.module = module_of(self.trainer)
        self.trainer_python = trainer_python
        self.limits = limits
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid run identity')
        self.run_dir = self.state/'training'/'runs'/run_id
        self.tasks = self._value('--tasks')
        self.out = self._value('--out')
        self.receipt_file = (f'{self.out}/{TRAINER_RECEIPT}' if self.out
                             else str(self.run_dir/TRAINER_RECEIPT))
        self.marker_file = (f'{self.out}/{PUBLISH_MARKER}' if self.out
                            else str(self.run_dir/PUBLISH_MARKER))
        # The probe is resolved by the caller so its interpreter error is reported as
        # such, and so the launch command uses an interpreter that actually ran.
        #
        # `self.trainer` is a full argv that starts with the interpreter, so the module is NOT at
        # index 1: `['python', '-m', 'eval.train_source_repair', ...]` has the `-m` flag there.
        # `module_of` reads the argv the way it is actually shaped.
        self.probe = probe if probe is not None else probe_trainer(
            trainer_python or interpreter_of(self.trainer), self.repo, module=self.module)
        self.flags = set(self.probe.flags or ())
        self.started = time.monotonic()
        self.started_at = time.time()
        self.process = None
        self.events = None
        self.lines = queue.Queue()
        self.trainer_log = None
        self.last_publish = 0.0
        self.signal_seen = None
        self.data = {'run_id': run_id, 'status': 'starting', 'stage': 'preparing',
                     'phase': 'Preparing the training worker', 'started_at': time.time(),
                     'pid': os.getpid(), 'max_steps': limits.get('max_steps', 0),
                     'max_examples': limits.get('max_examples', 0),
                     'max_seconds': max_seconds, 'steps': 0, 'examples_used': 0,
                     'examples_available': None, 'last_loss': None, 'terminal_reason': None,
                     'stop_reason': None, 'adapter_published': False, 'tasks': self.tasks,
                     'adapter_out': self.out, 'trainer_module': TRAINER_MODULE,
                     'trainer_flags_offered': sorted(self.flags),
                     'trainer_probe': self.probe.as_dict(),
                     'trainer_flags_omitted': [], 'scope': 'bounded local adapter training'}

    def _value(self, name):
        try:
            return self.trainer[self.trainer.index(name)+1]
        except (ValueError, IndexError):
            return None

    def publish(self, force=False):
        if not force and time.monotonic() - self.last_publish < PUBLISH_SECONDS:
            return
        self.data.update(updated_at=time.time(), active=self.data['status'] in ACTIVE,
                         elapsed_seconds=round(time.monotonic()-self.started, 1),
                         limits=self.limits)
        atomic_json(self.view, self.data)
        self.last_publish = time.monotonic()

    def event(self, kind, **fields):
        if self.events is not None:
            self.events.write(json.dumps({'event': kind, 'at': time.time(), **fields}) + '\n')
            self.events.flush()

    def stage(self, stage, phase, **fields):
        self.data.update(stage=stage, phase=phase, **fields)
        self.event('stage', stage=stage, phase=phase, **fields)
        self.publish(True)

    def stop_requested(self):
        return self.signal_seen is not None or \
            (read_json(self.stop, {}) or {}).get('run_id') == self.run_id

    def signalled(self, name):
        """Record a signal without doing work in the handler, then unwind the loop."""
        self.signal_seen = name
        self.event('worker_signal', signal=name)
        raise TrainingInterrupted(name)

    def command(self):
        """The trainer argv, minus any flag the trainer does not advertise.

        Every owned flag is dropped from the caller's argv and re-appended in one
        canonical order, so no flag is ever passed twice and no value is ever passed
        for a flag the trainer does not advertise.
        """
        owned = ('--base', '--tasks', '--out', '--split', '--max-seq-len', '--max-steps',
                 '--max-seconds', '--max-examples', '--block-size')
        omitted, argv, skip = [], [], False
        for token in self.trainer:
            if skip:
                skip = False
                continue
            if token in TRAINER_FLAGS and (token not in self.flags or token in owned):
                if token not in self.flags:
                    omitted.append(token)
                skip = True                                  # drop its value too
                continue
            argv.append(token)
        wanted = [('--base', self._value('--base')), ('--tasks', self.tasks),
                  ('--split', self._value('--split')), ('--out', self.out),
                  ('--max-seq-len', self._value('--max-seq-len')),
                  ('--max-steps', self._value('--max-steps')),
                  ('--max-examples', self._value('--max-examples')),
                  ('--block-size', self._value('--block-size')),
                  ('--max-seconds', str(int(self.max_seconds)))]
        for flag, value in wanted:
            if flag not in self.flags:
                continue
            if value is None:
                raise ValueError(f'{flag} has no value to pass to the trainer')
            argv += [flag, value]
        if self.trainer_python:
            argv[0] = self.trainer_python
        self.data['trainer_flags_omitted'] = omitted
        return argv

    def reap(self, name, wait=True):
        """Signal what is left of the group, so an orphaned compiler cannot survive."""
        if self.process is None:
            return
        pid = self.process.pid
        self.event('trainer_signal', signal=name, pid=pid)
        if os.name != 'nt':
            try:
                os.killpg(pid, signal.SIGTERM if name == 'TERM' else signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        elif self.process.poll() is None:
            self.process.terminate()
        if wait:
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                else:
                    self.process.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    def trainer_progress(self, line):
        """Stage progress from the trainer's own output: the only real source."""
        try:
            row = json.loads(line)
        except ValueError:
            return
        if not isinstance(row, dict):
            return
        if type(row.get('step')) is int:
            self.data['steps'] = row['step']
            if isinstance(row.get('loss'), (int, float)):
                self.data['last_loss'] = row['loss']
            self.data['stage'] = 'training'
            self.data['phase'] = f"Training: step {row['step']} of {self.data['max_steps']}"
            self.event('trainer_step', **{k: row[k] for k in ('step', 'loss', 'lr', 'epoch')
                                          if k in row})
        elif type(row.get('examples_used')) is int:
            self.data['examples_used'] = row['examples_used']
        elif row.get('paused'):
            self.stage('paused', 'Trainer paused: ' + str(row['paused']))
        self.publish()

    def check_stop(self):
        """One poll: has the stop file, a signal, or the wall clock ended the run?"""
        if self.stop_requested():
            self.event('stop_file', pgid=self.process.pid)
            self.reap('TERM')
            return 'stopped'
        if time.monotonic() >= self.started + self.max_seconds:
            self.event('limit_hit', limit='max_seconds', max_seconds=self.max_seconds)
            self.reap('TERM')
            return 'stopped'
        self.publish()
        return None

    def read_pipe(self):
        """Reader thread: keep the pipe empty so a full buffer cannot deadlock the run.

        Windows cannot poll a pipe with `select`, and blocking reads on the supervisor
        thread would stall the stop check, so one daemon thread owns the pipe for the
        whole run and hands lines to the supervisor through a queue.
        """
        try:
            for line in self.process.stdout:
                self.trainer_log.write(line)
                self.trainer_log.flush()
                self.lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self.lines.put(None)

    def drain(self, timeout=0.0):
        """Take whatever the trainer has printed, without blocking the supervisor."""
        end = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get_nowait()
            except queue.Empty:
                if time.monotonic() >= end:
                    return
                time.sleep(0.01)
                continue
            if line is None:
                return
            self.trainer_progress(line.rstrip('\n'))

    def supervise(self):
        """Wait for the trainer, tee its output, and honour stop and wall clock.

        The trainer's stdout is the progress source: a file shared between the child
        that writes it and the parent that reads it does not deliver lines on this
        platform, and a panel reading zero steps is exactly the failure that would hide.
        """
        while True:
            code = self.process.poll()
            self.drain()
            if code is not None:
                self.drain(1.0)                    # let the reader thread finish
                self.trainer_log.flush()
                return code, None
            cut = self.check_stop()
            if cut:
                return self.process.poll(), cut
            time.sleep(0.05)          # publish() throttles its own writes to 1 Hz

    def receipt(self):
        """The trainer's own receipt, when it exists for this run.

        The trainer writes `training_receipt.json` into its `--out` directory. The
        worker's own record is not consulted here: it cannot authorize anything.
        """
        value = read_json(Path(self.receipt_file), None)
        if not isinstance(value, dict) or 'published' not in value:
            return None
        return value

    def adapter(self):
        """Published only when the trainer wrote BOTH its receipt and its marker.

        The trainer publishes an adapter only after a clean stop in which at least one
        tensor changed, and it says so in `training_receipt.json` and by writing
        `PUBLISHED.json`. A non-zero exit alone means nothing here: the trainer exits
        non-zero on a run that trained but refused to publish, and on the deliberate
        refusal to overwrite an already-published adapter.
        """
        value = self.receipt()
        if not isinstance(value, dict) or not value.get('published'):
            return None, value
        marker = Path(self.marker_file)
        if not marker.exists():
            return None, value
        adapter = value.get('published_marker') or self.marker_file
        directory = Path(adapter).parent if str(adapter).endswith('.json') else Path(adapter)
        if not (directory/'adapter_model.safetensors').exists():
            return None, value
        # A marker that already existed when this run started, or that appeared while
        # it ran, is not this run's publication.
        if self.started_at is not None and marker.stat().st_mtime < self.started_at:
            self.event('stale_marker', marker=str(marker), mtime=marker.stat().st_mtime,
                       run_started=self.started_at)
            return None, value
        return str(directory), value

    def run(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events = (self.run_dir/'events.jsonl').open('a', encoding='utf-8')
        self.trainer_log = (self.run_dir/'trainer.log').open('w', encoding='utf-8')
        # Register the process group FIRST, before anything can return early. This is the
        # worker's identity, and a Stop that arrives while the worker is still deciding needs a
        # pgid to signal. Registering it after the already-published check left a window in which
        # the run was live but had no group on record, and the harness could not see it start.
        pgid = os.getpgid(0) if hasattr(os, 'getpgid') else os.getpid()
        atomic_json(self.pid, {'run_id': self.run_id, 'pid': os.getpid(), 'pgid': pgid,
                               'at': time.time()})
        # Re-running against a directory that already holds a published adapter exits
        # non-zero on purpose ("already published"). Surface it once, distinctly, so no
        # automatic retry can hammer a result the trainer refused to overwrite.
        if Path(self.marker_file).exists():
            self.event('already_published', marker=self.marker_file)
            self.data.update(terminal_reason=f"an adapter is already published at {self.out}; "
                                             f"move it aside deliberately before retraining")
            self.finish('already_published')
            return self.data
        argv = self.command()
        self.event('run_started', trainer=argv, limits=self.limits, max_seconds=self.max_seconds,
                   flags=sorted(self.flags), omitted=self.data['trainer_flags_omitted'])
        self.stage('preparing', 'Starting the trainer process group')
        try:
            self.process = subprocess.Popen(argv, cwd=self.repo, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                start_new_session=os.name != 'nt',            # its own killable group
                creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        except (OSError, ValueError) as exc:
            self.data.update(terminal_reason=f'the trainer could not be started: {exc}')
            self.finish('failed')
            return self.data
        self.data['trainer_pid'] = self.process.pid
        self.data['trainer_pgid'] = self.process.pid
        self.data['examples_available'] = read_examples(self.tasks)
        # THE LIVE TRANSITION. Nothing else ever set `running`, so the panel showed "starting"
        # for the whole run -- documented as "the worker's status file is fresh and the run is
        # live", but never reached. It is set here, in the same atomic publish that first carries
        # `trainer_pid`, so a poller that can see the trainer's pid can also see that it is live.
        self.data['status'] = 'running'
        self.stage('training', f'Training: step 0 of {self.data["max_steps"]}')
        reader = threading.Thread(target=self.read_pipe, daemon=True)
        reader.start()
        try:
            code, cut = self.supervise()
        except TrainingInterrupted:
            # Our own group was killed, so the trainer already has the signal. Reap the
            # group for orphaned compiler subprocesses, then record what happened: a run
            # cut off this way is stopped, never completed, never publishes an adapter.
            code = None
            self.reap('KILL', wait=False)
            self.consume_receipt()
            self.data.update(exit_code=code, terminal_reason='the training worker was signalled')
            self.finish('stopped')
            return self.data
        finally:
            reader.join(timeout=2)
        self.data['exit_code'] = code
        self.consume_receipt()
        if cut == 'stopped':
            self.reap('KILL', wait=False)
            try:
                user = self.stop_requested()
            except TrainingInterrupted:
                user = True                     # a signal arrived while we were stopping
            self.data.update(terminal_reason=(
                'stopped by the user' if user
                else f"wall-clock limit of {self.max_seconds}s reached"))
            self.finish('stopped')
            return self.data
        self.stage('publishing', 'Checking the trainer and its publish marker')
        adapter, declared = self.adapter()
        if adapter is not None:
            self.data.update(adapter=adapter, adapter_published=True,
                             terminal_reason='the trainer stopped cleanly and wrote its publish '
                                             'marker: ' +
                                             str((declared or {}).get('stop_reason') or 'clean stop'))
            self.finish('completed')
            return self.data
        stop_reason = str((declared or {}).get('stop_reason') or '').strip()
        if declared and not declared.get('published'):
            # The trainer's own refusal. A clean stop that changed no weight is the
            # common case, and it is not a wiring failure.
            self.data.update(terminal_reason='the trainer did not publish an adapter for this run'
                                             + (f': {stop_reason}' if stop_reason else ''))
        elif not declared:
            self.data.update(terminal_reason='the trainer wrote no receipt at '
                                             f'{self.receipt_file}'
                                             + (f' (exit code {code})' if code else ''))
        else:
            self.data.update(terminal_reason='the receipt claims publication but no publish '
                                             f'marker exists at {self.marker_file}')
        self.finish('failed')
        return self.data

    def consume_receipt(self):
        """Adopt the trainer's own numbers into the status view, never into a verdict."""
        declared = self.receipt()
        if not isinstance(declared, dict):
            return None
        metrics = declared.get('examples') if isinstance(declared.get('examples'), dict) else {}
        used = declared.get('examples_used')
        if type(used) is not int:
            used = metrics.get('kept')
        self.data.update(
            stop_reason=declared.get('stop_reason'),
            steps=declared.get('steps_run', self.data['steps']),
            examples_used=used if type(used) is int else self.data['examples_used'],
            examples_available=metrics.get('available', self.data.get('examples_available')),
            peak_gpu_gb=declared.get('peak_gpu_gb'),
            last_loss=declared.get('last_loss', self.data.get('last_loss')),
            weight_change=declared.get('weight_change'))
        return declared

    def finish(self, status):
        self.data.update(status=status, finished_at=time.time(), active=False)
        self.event('run_finished', status=status, terminal_reason=self.data['terminal_reason'],
                   steps=self.data['steps'], examples_used=self.data['examples_used'])
        self.publish(True)
        # The worker's own record is written beside the run, NOT to the trainer's
        # training_receipt.json -- that file is the trainer's evidence and is read back
        # to decide whether anything was published.
        record = {**self.data, 'kind': 'training-run-receipt-v1',
                  'trainer_receipt': self.receipt_file,
                  'adapter_published': bool(self.data.get('adapter_published')),
                  'scope_note': 'This receipt records what the run did. It is not evidence that '
                                'training improved the model; held-out evaluation is separate.'}
        atomic_json(self.run_dir/'run.json', record)
        atomic_json(self.view.parent/'training-receipt.json', record)
        for stream in (self.events, self.trainer_log):
            if stream is not None:
                stream.close()
        self.events = self.trainer_log = None


def worker_main(argv=None):
    ap = argparse.ArgumentParser(description='Supervise one bounded local training run.')
    ap.add_argument('--worker', action='store_true')
    ap.add_argument('--state', type=Path, default=Path.home()/'decomp/local-research')
    ap.add_argument('--view', type=Path, required=True)
    ap.add_argument('--stop', type=Path, required=True)
    ap.add_argument('--pid', type=Path, required=True)
    ap.add_argument('--run-id', required=True)
    # The working directory for the probe and the trainer: the checkout, because the trainer is
    # `python -m eval.<module>` and `eval` is not importable from the game repo. Defaulting to
    # the game repo asked the probe to import a module it could not reach and blamed the trainer.
    ap.add_argument('--repo', type=Path, default=None)
    ap.add_argument('--trainer-python')
    ap.add_argument('--interpreter-fallback', action='append', default=[],
                    help='an interpreter to try if the configured one cannot be run; '
                         'the one that answered is reported in the run record')
    ap.add_argument('--max-seconds', type=int, required=True)
    ap.add_argument('--trainer', nargs=argparse.REMAINDER, required=True,
                    help='the trainer argv; it must start with the interpreter')
    args = ap.parse_args(argv)
    if args.trainer and args.trainer[0] == '--':
        args.trainer = args.trainer[1:]
    if str(args.state).startswith('/mnt/'):
        ap.error('Training state must be on the WSL filesystem')
    limits = {}
    for token, key in (('--max-steps', 'max_steps'), ('--max-examples', 'max_examples')):
        if token in args.trainer:
            try:
                limits[key] = int(args.trainer[args.trainer.index(token)+1])
            except (IndexError, ValueError):
                ap.error(f'{token} needs an integer value')
    args.state.mkdir(parents=True, exist_ok=True)
    if args.repo is None:
        args.repo = Path(trainer_workdir())
    # Resolve the interpreter before building anything: `python` does not exist in this
    # WSL install, and a probe that cannot run must not read as "the trainer supports
    # nothing". The interpreter that answered is the one the trainer is launched with.
    probe, interpreter = trainer_flags(interpreter_of(args.trainer, args.trainer_python), args.repo,
                                       module=module_of(args.trainer),
                                       fallbacks=args.interpreter_fallback)
    if interpreter:
        args.trainer_python = interpreter
    run = Training(state=args.state, view=args.view, stop=args.stop, pid=args.pid,
                   run_id=args.run_id, trainer=args.trainer, repo=args.repo,
                   trainer_python=args.trainer_python, max_seconds=args.max_seconds,
                   limits=limits, probe=probe)
    try:
        lock, release = worker_lock(args.state/'training'/'worker.lock')
    except RuntimeError as exc:
        ap.error(str(exc))
    try:
        missing = run.probe.missing if run.probe.ok else []
        if not run.probe.ok or missing:
            run.run_dir.mkdir(parents=True, exist_ok=True)
            run.events = (run.run_dir/'events.jsonl').open('a', encoding='utf-8')
            run.trainer_log = (run.run_dir/'trainer.log').open('a', encoding='utf-8')
            if not run.probe.ok:
                reason = (f'the trainer could not be probed: {run.probe.error}'
                          + (f' (tried {", ".join(run.probe.tried)})' if run.probe.tried else ''))
                payload = {'status': 'failed', 'trainer_probe_error': run.probe.error,
                           'tried': run.probe.tried}
            else:
                reason = ('the trainer at ' + str(run.probe.interpreter) + ' does not offer '
                          + ', '.join(missing)
                          + '; the panel will not claim a limit the trainer cannot enforce')
                payload = {'status': 'failed', 'missing_trainer_flags': missing,
                           'interpreter': run.probe.interpreter}
            run.data.update(terminal_reason=reason, trainer_probe=run.probe.as_dict())
            run.stage('preparing', 'Checking which flags the trainer advertises')
            run.finish('failed')
            print(json.dumps(payload))
            return 2
        install_signal_handlers(run)
        result = run.run()
    finally:
        release()
    print(json.dumps({k: result.get(k) for k in
                      ('status', 'steps', 'examples_used', 'terminal_reason')}))
    return 0 if result['status'] == 'completed' else int(result['status'] == 'failed')


def worker_lock(path):
    """Copy of the research worker's Linux advisory lock, portable for the tests.

    The real worker runs under WSL where `fcntl.flock` is available, exactly as
    `eval/local_research.py` uses it: one worker at a time survives a dashboard
    restart. On a non-POSIX host there is no flock, so exclusion falls back to
    exclusive file creation and the run still refuses a duplicate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        marker = path.with_suffix('.lockfile')
        handle = None
        for _ in range(5):
            try:
                handle = marker.open('x')
                break
            except FileExistsError:
                # A marker left by a process that no longer exists must not wedge the
                # panel forever, so the holder has to be identifiable.
                try:
                    holder = int(marker.read_text().strip() or 0)
                except ValueError:
                    holder = 0
                if holder and pid_alive(holder):
                    raise RuntimeError('A training worker is already running') from None
                try:
                    marker.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    # Windows refuses to unlink a file another handle still holds, which
                    # is what a marker left by a killed process looks like. If the holder
                    # is gone the marker is stale, and a stale marker must never wedge
                    # the panel: say so instead of failing with a raw OS error.
                    raise RuntimeError(
                        f'a stale training-worker lock at {marker} could not be cleared '
                        f'({exc}); remove it once no training worker is running') from None
        if handle is None:
            raise RuntimeError('A training worker is already running')
        handle.write(str(os.getpid()))
        handle.flush()
        return handle, lambda: (handle.close(), marker.unlink(missing_ok=True))
    handle = path.open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError('A training worker is already running') from None
    return handle, handle.close


def install_signal_handlers(run):
    """Turn a group kill into a recorded stop instead of a silent disappearance.

    The stop file is the graceful path; this covers a direct signal, so a run that
    was cut off still says `stopped` in its receipt rather than leaving the panel to
    infer an interruption. Python runs the handler between bytecodes, so it is safe
    for the handler to raise.
    """
    def handler(signum, frame):
        run.signalled(signal.Signals(signum).name)
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        signo = getattr(signal, name, None)
        if signo is not None:
            try:
                signal.signal(signo, handler)
            except (ValueError, OSError):
                pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--signal' in argv:
        ap = argparse.ArgumentParser(description='Signal a process group inside WSL.')
        ap.add_argument('--signal', choices=('TERM', 'KILL'), required=True)
        ap.add_argument('--pgid', type=int, required=True)
        args = ap.parse_args(argv)
        return _signal_group(args.pgid, args.signal)
    return worker_main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
