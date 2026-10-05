"""Dashboard bridge to ONE bounded narrow-RSI experiment.

STATE CONTRACT (fixed; the experiment runner owns this file, the dashboard only reads it):

    eval/results/narrow-rsi-20260921/rsi_state.json
        experiment_id, stage, generation, parent, candidate, hypothesis,
        evidence_status, budget {caps, spent, remaining}, evaluation {panel, functions,
        coverage}, gate_reason, stop_requested, updated_at

The file may not exist before the first run and any individual key may be missing, so
`get()` normalises every field the panel reads (`PANEL_FIELDS`) and reports
`present: false` instead of raising. The panel consumes exactly that key set and a test
asserts it, so the projection and the HTML cannot drift apart.

LAUNCH -- one copy, background, independent of the HTTP request. This reuses the
mechanism the research and training panels already use: `subprocess.Popen` of a WSL
command line that runs the runner as a module in THIS checkout (`wsl.exe -d <distro>
--cd <checkout> -e <python> -m eval.narrow_rsi --state ... --stop ... --pid ... --run-id
... --minutes ...`), with stdout+stderr appended to a log file beside the state,
`start_new_session` under POSIX and `CREATE_NO_WINDOW` on Windows. The request returns as
soon as the process exists, and the runner publishes its own state file, so a dashboard
restart adopts a live run instead of forgetting it. The Windows-side launcher pid is
recorded in `rsi-launch.json`; the runner records its OWN pid and process-group id in
`rsi.pid`, under the `--run-id` it was given, because the group Stop must signal lives
inside WSL, not on this side.

The runner module does not exist in this checkout yet. Rather than spawn a process that
can only die with `ModuleNotFoundError`, `start()` refuses with the declared command in
the message when the runner file is absent, and the panel shows the runner's log tail when
a process it launched did exit. The declared entry point and its flags are in one place
(`RUNNER_MODULE`, `RUNNER_SOURCE`, `RUNNER_FLAGS`).

STOP -- two bounded mechanisms, neither of which can select an unrelated job:
  1. `STOP` is written into the run directory. The runner polls it and unwinds at its next
     stage boundary; this is the graceful path and it always happens.
  2. If `rsi.pid` names a process group for THIS run -- its run id must equal the run id
     this dashboard launched, so a pid file left behind by another run cannot be selected
     -- that one group is signalled -- `kill -TERM -<pgid>`, escalating to `-KILL` if it is
     still alive after the grace period -- by reusing `eval.training_control.terminate_group`.
     The negative operand is one group addressed by number: there is no pattern match, no
     `pkill`, no WSL shutdown, no GPU reset and no campaign kill anywhere in this path.
     Signalling an already-dead group is a success, not an error. A run started outside the
     dashboard is therefore stopped by its `STOP` file only -- gracefully, never by a guess.
  3. A new run deletes a leftover `STOP` file first, so the previous run's stop request
     cannot end the next one.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

from eval.local_research import atomic_json, read_json

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT/'eval/results/narrow-rsi-20260921'      # the fixed contract path
STATE_NAME = 'rsi_state.json'
STOP_NAME = 'STOP'
PID_NAME = 'rsi.pid'                  # written by the RUNNER inside WSL
LAUNCH_NAME = 'rsi-launch.json'       # written by this dashboard when it launches

# The declared runner: a module of THIS checkout executed by the WSL interpreter, plus
# every flag name the runner must implement. `--pid` is how Stop learns the group.
RUNNER_MODULE = 'eval.narrow_rsi'
RUNNER_SOURCE = ROOT/'eval/narrow_rsi.py'
RUNNER_FLAGS = ('--state', '--stop', '--pid', '--run-id', '--minutes')

# Stage vocabulary from the contract. Every stage that is not listed as terminal counts as
# running, so a stage this module has never heard of refuses a second start instead of
# racing one.
IDLE_STAGE = 'idle'
ACTIVE_STAGES = ('frozen', 'research', 'verify', 'assemble', 'train', 'candidate-frozen',
                 'evaluate', 'accept')
TERMINAL_STAGES = ('done', 'stopped', 'failed', 'keep-parent', 'inconclusive')

DEFAULT_MINUTES, MAX_MINUTES = 30, 120   # the research panel's own wall-clock ceiling
# A stage (training, a frozen panel) legitimately runs this long between state writes, so a
# short window would both mislabel a live run and re-enable Start against it.
STALE_SECONDS = 900
PROBE_SECONDS = 30        # how long one process-group liveness probe is trusted
KILL_GRACE_SECONDS = 10.0

# Exactly what eval/progress_rsi.js reads. `phase` is dashboard prose; everything else is
# the runner's own state or a derived flag.
PANEL_FIELDS = ('present', 'experiment_id', 'stage', 'generation', 'parent', 'candidate',
                'hypothesis', 'evidence_status', 'budget', 'evaluation', 'gate_reason',
                'stop_requested', 'updated_at', 'age_seconds', 'active', 'stale',
                'terminal', 'stop_pending', 'controls_enabled', 'phase', 'limits',
                'last_error')


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _counts(raw):
    """A `{kind: number}` mapping with anything unusable dropped, never guessed."""
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in ((_text(k), _number(v)) for k, v in raw.items())
            if key and value is not None}


def _budget(raw):
    """caps/spent/remaining for each budget kind the runner declares.

    `remaining` is filled from cap-minus-spent only where the runner did not state it: that
    is arithmetic on the runner's own numbers, not an invented allowance.
    """
    raw = raw if isinstance(raw, dict) else {}
    caps, spent, remaining = _counts(raw.get('caps')), _counts(raw.get('spent')), \
        _counts(raw.get('remaining'))
    for kind, cap in caps.items():
        if kind not in remaining and kind in spent:
            remaining[kind] = max(0, cap-spent[kind])
    return {'caps': caps, 'spent': spent, 'remaining': remaining,
            'kinds': sorted(set(caps) | set(spent) | set(remaining))}


def _evaluation(raw):
    raw = raw if isinstance(raw, dict) else {}
    return {'panel': _text(raw.get('panel')), 'functions': _number(raw.get('functions')),
            'coverage': _number(raw.get('coverage'))}


class RsiControl:
    """Read-only state projection plus Start/Stop for one narrow-RSI experiment."""

    def __init__(self, directory=RUN_DIR, distro='Ubuntu',
                 python='/home/grant/decomp/sbk1/.venv/bin/python', *, enabled=True,
                 launch=True, runner=RUNNER_SOURCE, wsl='wsl.exe',
                 grace=KILL_GRACE_SECONDS):
        self.directory = Path(directory)
        self.distro, self.python, self.enabled = distro, python, enabled
        self.launch, self.runner, self.wsl, self.grace = launch, Path(runner), wsl, grace
        self.lock = threading.Lock()
        self.process = self.log = None
        self.last_signals = []
        self._probe = None

    # --- paths -----------------------------------------------------------------
    @property
    def state_file(self):
        return self.directory/STATE_NAME

    @property
    def stop_file(self):
        return self.directory/STOP_NAME

    @property
    def pid_file(self):
        return self.directory/PID_NAME

    @property
    def launch_file(self):
        return self.directory/LAUNCH_NAME

    # --- projection ------------------------------------------------------------
    def get(self):
        """The panel's whole world. Never raises on a missing, partial or corrupt file."""
        record = read_json(self.state_file, None)
        present = isinstance(record, dict)
        value = record if present else {}
        stage = _text(value.get('stage'))
        key = stage or (IDLE_STAGE if not present else 'unknown')
        updated = _number(value.get('updated_at'))
        age = max(0.0, time.time()-updated) if updated is not None else None
        terminal = key in TERMINAL_STAGES
        active = present and not terminal and key != IDLE_STAGE
        # An old heartbeat alone is not proof of death: ask the runner's own group first,
        # and only the group whose run id this dashboard actually launched.
        pgid = self.run_group()
        stale = bool(active and age is not None and age > STALE_SECONDS)
        if stale and pgid is not None and self.group_live(pgid):
            stale = False
        active = active and not stale
        stop_file = self.stop_file.exists()
        stop_requested = bool(value.get('stop_requested'))
        state = {
            'present': present,
            'experiment_id': _text(value.get('experiment_id')),
            'stage': key,
            'generation': _text(value.get('generation')),
            'parent': _text(value.get('parent')),
            'candidate': _text(value.get('candidate')),
            'hypothesis': _text(value.get('hypothesis')) or '',
            'evidence_status': _text(value.get('evidence_status')) or 'unknown',
            'budget': _budget(value.get('budget')),
            'evaluation': _evaluation(value.get('evaluation')),
            'gate_reason': _text(value.get('gate_reason')) or '',
            'stop_requested': stop_requested,
            'updated_at': updated,
            'age_seconds': age,
            'active': active,
            'stale': stale,
            'terminal': terminal,
            'stop_pending': bool(present and not terminal
                                 and (stop_file or stop_requested)),
            'controls_enabled': self.enabled,
            'phase': self.phase(key, present, active, stale),
            'limits': {'minutes': DEFAULT_MINUTES, 'max_minutes': MAX_MINUTES},
            'last_error': None,
        }
        if state['stop_pending'] and active:
            state['phase'] = 'Stop requested: the runner stops at its next stage boundary.'
        if self.process is not None and self.process.poll() is not None and not active:
            # A process WE launched exited without an active state: say so with its own
            # output, rather than showing an idle panel as if nothing had been started.
            state['last_error'] = self.log_tail()
            if not present:
                state['phase'] = 'The runner exited before it published any state.'
        return state

    @staticmethod
    def phase(key, present, active, stale):
        if not present:
            return 'No narrow-RSI experiment has run yet.'
        if stale:
            return f'No state update for over {STALE_SECONDS // 60} minutes; the runner may have exited.'
        verdicts = {'done': 'The experiment finished. The gate decision is below.',
                    'stopped': 'The run was stopped; nothing was promoted.',
                    'failed': 'The run failed. See the reported error.',
                    'keep-parent': 'The candidate was rejected; the parent is kept.',
                    'inconclusive': 'The run ended inconclusive; no candidate was promoted.'}
        if key in verdicts:
            return verdicts[key]
        if active:
            return f'Running stage {key}.' if key in ACTIVE_STAGES else \
                f'Running a stage this panel does not know: {key}.'
        return 'Waiting for the runner to publish a stage.'

    def live_process(self):
        return self.process is not None and self.process.poll() is None

    def known_group(self):
        """The `(run_id, pgid)` the RUNNER recorded for itself -- never a guess.

        A pgid is returned only when it is a positive integer; a run id the runner omitted
        stays None, and Stop then writes the file without signalling anything.
        """
        record = read_json(self.pid_file, None)
        if not isinstance(record, dict):
            return None, None
        pgid = record.get('pgid')
        if isinstance(pgid, bool) or not isinstance(pgid, int) or pgid <= 0:
            return _text(record.get('run_id')), None
        return _text(record.get('run_id')), pgid

    def launched_run_id(self):
        """The `--run-id` THIS dashboard handed the runner; it survives a dashboard restart."""
        record = read_json(self.launch_file, None)
        return _text(record.get('run_id')) if isinstance(record, dict) else None

    def run_group(self):
        """The pgid this dashboard may probe or signal, or None.

        The identity rule is the whole safety property: the runner is required to copy the
        `--run-id` it was given into `rsi.pid`, and the dashboard signals that group only
        when it matches the run id in its own `rsi-launch.json`. A pid file left by another
        run therefore cannot be signalled, and a run started outside the dashboard is
        stopped by its `STOP` file only -- gracefully, never by a guessed pid.
        """
        record_id, pgid = self.known_group()
        ours = self.launched_run_id()
        return pgid if pgid is not None and ours is not None and record_id == ours else None

    def group_live(self, pgid):
        """Is that ONE group still alive? Cached, and only asked when a heartbeat is old."""
        now = time.monotonic()
        if self._probe and self._probe[0] == pgid and now-self._probe[1] < PROBE_SECONDS:
            return self._probe[2]
        try:
            alive = bool(self.group_alive(pgid))
        except (OSError, subprocess.SubprocessError, ValueError):
            alive = False
        self._probe = (pgid, now, alive)
        return alive

    def group_alive(self, pgid):
        """`kill -0 -<pgid>` inside WSL: the POSIX "is any process in this group" test.

        `sh -c` is used because `wsl.exe -e kill -0 -<pgid>` treats the negative operand as
        its own option. The probe joins no group, so it cannot report a dead group alive.
        """
        if os.name != 'nt':
            from eval.training_control import group_alive
            return group_alive(pgid)
        argv = [self.wsl, '-d', self.distro, '-e', 'sh', '-c', f'kill -0 -{int(pgid)}']
        return self._run(argv).returncode == 0

    def send_group_signal(self, pgid, name):
        """`kill -<sig> -<pgid>`: one group by number. Never a pattern, never a pkill."""
        if os.name != 'nt':
            try:
                os.killpg(int(pgid), signal.SIGTERM if name == 'TERM' else signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            return
        self._run([self.wsl, '-d', self.distro, '-e', 'sh', '-c',
                   f'kill -{"TERM" if name == "TERM" else "KILL"} -{int(pgid)}'])

    @staticmethod
    def _run(argv, timeout=15):
        return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def log_tail(self, limit=2000):
        path = Path(self.log) if self.log else None
        if path is None or not path.exists():
            return None
        try:
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size-limit))
                text = stream.read().decode('utf-8', errors='replace').strip()
        except OSError:
            return None
        return text or None

    # --- control ---------------------------------------------------------------
    def __call__(self, action, options):
        if not self.enabled:
            raise ValueError('Narrow-RSI controls are disabled in read-only mode')
        if action not in ('start', 'stop') or not isinstance(options, dict):
            raise ValueError('Narrow-RSI action must be start or stop')
        with self.lock:
            current = self.get()
            return self.stop(current, options) if action == 'stop' \
                else self.start(current, options)

    def command(self, run_id, minutes):
        """The declared launch argv, in the spelling this host needs."""
        from eval.progress_app import wsl_path
        args = [self.python, '-m', RUNNER_MODULE,
                '--state', wsl_path(self.state_file.resolve()),
                '--stop', wsl_path(self.stop_file.resolve()),
                '--pid', wsl_path(self.pid_file.resolve()),
                '--run-id', run_id, '--minutes', str(minutes)]
        return ([self.wsl, '-d', self.distro, '--cd', wsl_path(ROOT), '-e'] + args
                if os.name == 'nt' else args)

    def start(self, current=None, options=None):
        current = self.get() if current is None else current
        options = options or {}
        if set(options) - {'minutes'}:
            raise ValueError('Unknown narrow-RSI option')
        minutes = options.get('minutes', DEFAULT_MINUTES)
        if type(minutes) is not int or not 1 <= minutes <= MAX_MINUTES:
            raise ValueError(f'Minutes must be an integer from 1 to {MAX_MINUTES}')
        # The duplicate guard, checked before anything can be launched: the runner's own
        # published state first, then the process THIS dashboard started.
        if current['active'] or self.live_process():
            raise RuntimeError('A narrow-RSI experiment is already running')
        self.directory.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        command = self.command(run_id, minutes)
        log = self.directory/f'rsi-worker-{run_id}.log'
        if not self.launch:
            return {'run_id': run_id, 'minutes': minutes, 'command': command,
                    'log': str(log), 'launched': False}
        if not self.runner.exists():
            raise ValueError(f'the declared narrow-RSI runner {self.runner} is not present in '
                             f'this checkout, so nothing was launched; the declared command is '
                             + ' '.join(command))
        if self.stop_file.exists():
            self.stop_file.unlink()      # a previous run's stop must not end this one
        self.log = log
        with log.open('wb') as output:
            self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                start_new_session=os.name != 'nt')
        atomic_json(self.launch_file, {'run_id': run_id, 'launcher_pid': self.process.pid,
                                       'minutes': minutes, 'started_at': time.time(),
                                       'command': command})
        return self.get()

    def stop(self, current=None, options=None):
        """Idempotent: always asks politely, signals only this run's own group."""
        current = self.get() if current is None else current
        if options:
            raise ValueError('Stop accepts no options')
        record_id, _ = self.known_group()
        ours = self.launched_run_id()
        pgid = self.run_group()
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_json(self.stop_file, {'requested_at': time.time(),
                                     'run_id': ours or record_id or current.get('experiment_id'),
                                     'requested_by': 'progress dashboard'})
        if pgid is not None:
            from eval.training_control import terminate_group
            try:
                self.last_signals = terminate_group(
                    pgid, grace=self.grace, alive=lambda: self.group_alive(pgid),
                    send=lambda name: self.send_group_signal(pgid, name))
            except (OSError, subprocess.SubprocessError, ValueError):
                self.last_signals = []      # the STOP file above is already the request
        return self.get()
