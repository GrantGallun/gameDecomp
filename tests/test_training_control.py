"""Training-control tests: real processes, real signals, no mocked killpg.

The dummy trainer below stands in for `eval/train_source_repair`'s documented CLI. It
reads the same flags, prints the same JSON step lines, and writes the same
`training_receipt.json` + `PUBLISHED.json` publish gate. Nothing here trains anything,
touches a GPU, or downloads anything.

Two group-kill tests are provided. One runs everywhere and proves the signal reaches
the real WSL-side group through `kill -TERM -<pgid>`; the other drives the control
plane's own launch path end to end.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eval.progress_app import handler
from eval.training_control import (DEFAULT_BLOCK_SIZE, DEFAULT_EXAMPLES, DEFAULT_MINUTES,
                                   DEFAULT_SEQ_LEN, DEFAULT_STEPS, MAX_EXAMPLES, MAX_MINUTES,
                                   MAX_SEQ_LEN, MAX_STEPS, PUBLISH_MARKER, REQUIRED_TRAINER_FLAGS,
                                   TRAINER_MODULE, TRAINER_RECEIPT, TrainingControl,
                                   group_kill_command, pid_alive, trainer_flags)

ROOT = Path(__file__).resolve().parents[1]
WSL_PYTHON = '/usr/bin/python3'

# A stand-in trainer. It blocks until the allow file appears, prints three step
# lines, and then writes the receipt and publish marker that decide the outcome:
#   DUMMY_PUBLISH=1  clean stop, weights changed   -> receipt published + marker
#   DUMMY_PUBLISH=0  clean stop, no weight change  -> receipt not published, exit 1
#   DUMMY_NORECEIPT=1                              -> exit 0 with no receipt at all
DUMMY = '''
import argparse, json, os, signal, subprocess, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("--base"); ap.add_argument("--tasks"); ap.add_argument("--out", required=True)
ap.add_argument("--split", default="train")
ap.add_argument("--max-seq-len", type=int, default=0)
ap.add_argument("--max-steps", type=int, default=-1)
ap.add_argument("--max-seconds", type=float, default=0)
ap.add_argument("--max-examples", type=int, default=0)
ap.add_argument("--block-size", type=int, default=0)
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
print(json.dumps({"started": True, "argv": sys.argv[1:]}), flush=True)
if os.environ.get("DUMMY_GRANDCHILD"):
    # Deliberately NOT its own session: this grandchild stays in the trainer's process
    # group, which is the group Stop must empty.
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import os,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_DFL);"
         "open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(600)",
         os.environ["DUMMY_GRANDCHILD"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({"spawned": child.pid}), flush=True)
def bye(*_):
    raise SystemExit(143)
for name in ("SIGTERM", "SIGINT"):
    try:
        signal.signal(getattr(signal, name), bye)
    except Exception:
        pass
allow = os.environ.get("TRAIN_PROBE_ALLOW", os.environ.get("DUMMY_ALLOW", "/nonexistent"))
while not os.path.exists(allow):
    time.sleep(0.05)
for step in range(1, 4):
    print(json.dumps({"step": step, "loss": 0.5 / step, "lr": 1e-4, "epoch": 0}), flush=True)
    time.sleep(0.05)
publish = os.environ.get("DUMMY_PUBLISH", "1") == "1"
if os.environ.get("DUMMY_NORECEIPT", "0") != "1":
    receipt = {"schema_version": 1, "out": a.out, "split": a.split,
               "steps_run": 3, "stop_reason": "step budget" if publish else
               "no weight changed (3 step(s), 0 tensor(s) modified)",
               "published": publish,
               "published_marker": os.path.join(a.out, "PUBLISHED.json") if publish else None,
               "peak_gpu_gb": 0.5,
               "weight_change": {"tensors_compared": 4, "tensors_changed": 4 if publish else 0,
                                 "any_change": publish},
               "examples": {"available": 5, "kept": 3, "dropped_for_length": 0},
               "last_loss": 0.1667}
    if publish and os.environ.get("DUMMY_MARKER_MISSING", "0") != "1":
        open(os.path.join(a.out, "adapter_model.safetensors"), "w").write("weights")
        open(os.path.join(a.out, "PUBLISHED.json"), "w").write(json.dumps(
            {"published_at": int(time.time()), "steps": 3, "out": a.out}))
    json.dump(receipt, open(os.path.join(a.out, "training_receipt.json"), "w"))
sys.exit(0 if publish and os.environ.get("DUMMY_NORECEIPT", "0") != "1" else 1)
'''


# --- harness -------------------------------------------------------------------
def write_trainer(directory):
    directory.mkdir(parents=True, exist_ok=True)
    script = directory/'dummy_trainer.py'
    script.write_text(DUMMY, encoding='utf-8')
    return script


def repo_for_worker():
    """The repo as the worker's own filesystem sees it.

    Under WSL that is `/home/...`-style; on a Windows host running these tests it is
    the native path, because the worker here *is* the Windows interpreter.
    """
    return str(ROOT) if os.name == 'nt' else ROOT.as_posix()


def native(path):
    """`/mnt/c/...` spelled the way a Windows-hosted worker needs it; identity on WSL."""
    if os.name != 'nt':
        return path
    match = re.match(r'^/mnt/([a-zA-Z])/(.*)$', path)
    return f'{match.group(1).upper()}:\\{match.group(2)}'.replace('/', '\\') if match else path


def wire(control, script, *, run_id='run', env=None):
    """The exact worker argv the control plane would launch, aimed at the stand-in.

    Only the trainer command, the repo root and the mount prefix are adapted: the
    worker, its state files, its limits and its flag probe are the ones the control
    plane really builds, so the test exercises the wiring rather than a restatement.
    """
    plan = control('start', {'minutes': 5, 'max_steps': 4, 'max_examples': 8})
    argv = plan['worker']
    argv[argv.index('--trainer-python')+1] = sys.executable
    argv[argv.index('--repo')+1] = repo_for_worker()
    head = plan['trainer'][:4]                      # interpreter, -m, module, --base
    assert head[1:3] == ['-m', TRAINER_MODULE], head
    tail = plan['trainer'][3:]                      # --base and everything after it
    assert tail[0] == '--base' and tail[2] == '--tasks', tail
    argv[argv.index('--trainer')+1:] = [sys.executable, str(Path(script).resolve()), *tail]
    argv[argv.index('--run-id')+1] = run_id
    argv = [native(token) for token in argv]
    environment = {**os.environ, 'PYTHONPATH': repo_for_worker(),
                   'DUMMY_ALLOW': str(control.directory/'allow'), **(env or {})}
    return plan, argv, environment


def launch(control, script, *, run_id='run', env=None, before_start=None):
    """Spawn the worker chain. `before_start` runs after the plan exists, before it does."""
    plan, argv, environment = wire(control, script, run_id=run_id, env=env)
    if before_start is not None:
        before_start(plan)
    process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                               env=environment,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert wait_for(lambda: (control.directory/'training.pid').exists(), 30), \
        'the worker never registered its process group'
    return plan, process


def wait_for(predicate, timeout=30.0, interval=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def wsl_alive(pid, wsl='wsl.exe', distro='Ubuntu'):
    """`os.kill(pid, 0)` cannot see a WSL pid, so liveness is asked inside WSL."""
    try:
        return subprocess.run([wsl, '-d', distro, '-e', 'kill', '-0', str(int(pid))],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                              ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def wait_wsl_dead(pid, timeout=25.0):
    wait_for(lambda: not wsl_alive(pid), timeout=timeout)
    return not wsl_alive(pid)


def wait_terminal(control, timeout=40.0):
    wait_for(lambda: control.get()['status'] not in ('starting', 'running', 'stopping'), timeout)
    return control.get()


def allow(control):
    (control.directory/'allow').write_text('')


@pytest.fixture(autouse=True)
def _no_orphan_workers():
    """Kill any worker chain this test started, so a temp directory can be removed.

    The tests spawn real processes on purpose; without this a run that ends between
    assertions keeps its stdout pipe open and the next test's tmp_path cleanup fails
    on Windows with an access error that looks like a fixture bug. The match is on the
    child's own module or script name and never on this process, whose command line
    happens to contain the name of this test file.
    """
    yield
    listing = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         "$me = $PID; Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
         "Where-Object { $_.ProcessId -ne $me -and $_.CommandLine -match "
         "'eval\\.training_control|dummy_trainer\\.py' } | "
         "ForEach-Object { \"$($_.ProcessId)\" }"],
        capture_output=True, text=True, timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for token in (listing.stdout or '').split():
        if token.isdigit():
            subprocess.run(['taskkill', '/F', '/T', '/PID', token], capture_output=True,
                           timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def controller(tmp_path, **kw):
    """A control plane whose state root is the same directory, spelled the WSL way."""
    directory = tmp_path/'projection'
    directory.mkdir(parents=True, exist_ok=True)
    return TrainingControl(directory, python=sys.executable, state=_posix(tmp_path/'lab'),
                           launch=False, log=directory/'control.log', **kw)


# --- 1. Stop terminates the whole GROUP ----------------------------------------
def test_stop_terminates_the_wsl_process_group_including_a_grandchild(tmp_path):
    """`kill -TERM -<pgid>` inside WSL must reach the trainer AND its child.

    A trainer spawns compiler subprocesses, so stopping only the direct child would
    leave work running. The dummy here spawns a grandchild in its own process group,
    the worker registers that group, and Stop must empty it -- the same delivery the
    real trainer's compiler children would receive.
    """
    script = write_trainer(tmp_path)
    grandchild = tmp_path/'grandchild.pid'
    projection_dir = tmp_path/'projection'
    projection_dir.mkdir(parents=True, exist_ok=True)
    control = TrainingControl(projection_dir, python=WSL_PYTHON, state=_posix(tmp_path/'lab'),
                              launch=False, log=projection_dir/'control.log')
    state = f'/home/grant/decomp/training-test-{os.getpid()}-{int(time.time())}'
    projection = _posix(tmp_path/'projection')
    run_id = 'group'
    argv = [control.wsl, '-d', control.distro, '--cd', _posix(ROOT), '-e', 'env',
            f'PYTHONPATH={_posix(ROOT)}', f'DUMMY_ALLOW={_posix(tmp_path/"allow")}',
            f'DUMMY_GRANDCHILD={_posix(grandchild)}', WSL_PYTHON,
            '-m', 'eval.training_control', '--worker', '--state', state,
            '--view', f'{projection}/training-status.json',
            '--stop', f'{projection}/training-stop.json',
            '--pid', f'{projection}/training.pid', '--run-id', run_id, '--repo', _posix(ROOT),
            '--trainer-python', WSL_PYTHON, '--max-seconds', '300',
            '--trainer', WSL_PYTHON, _posix(script),
            '--base', f'{state}/model', '--tasks', f'{state}/tasks.jsonl', '--split', 'train',
            '--out', f'{state}/adapters/{run_id}', '--max-seq-len', '512', '--max-steps', '400',
            '--max-seconds', '300', '--max-examples', '8', '--block-size', '1']
    # WSL does not inherit a Windows-side environment, so the dummy's settings are
    # passed through `env` explicitly rather than through this process's os.environ.
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        if not wait_for(lambda: grandchild.exists() or process.poll() is not None, 60):
            process.kill()
            listing = subprocess.run([control.wsl, '-d', control.distro, '-e', 'ls', '-la', state],
                                     capture_output=True, text=True,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            pytest.fail('the WSL worker chain did not start: ' +
                        repr(process.stdout.read()[-800:]) + ' state=' + listing.stdout[:400])
        if not grandchild.exists():
            pytest.fail('the WSL worker exited early: ' +
                        repr(process.stdout.read()[-600:]) + ' ARGV=' + repr(argv))
        projection_dir = tmp_path/'projection'
        pid_file = wait_for(lambda: (projection_dir/'training.pid').exists(), 20) and json.loads(
            (projection_dir/'training.pid').read_text())
        assert pid_file, 'the worker never registered its process group'
        pgid = pid_file['pgid']
        trainer_pid = wait_for(lambda: _trainer_pid(projection_dir), 20)
        assert trainer_pid, 'the trainer never started'
        assert wsl_alive(trainer_pid) and wsl_alive(int(grandchild.read_text().strip()))
        # The pid registered for the group is the TRAINER's session, so the kill must
        # reach the worker's child and its grandchild without touching this test.
        control.process = SimpleNamespace(poll=lambda: None)
        stopped = control('stop', {})
        assert stopped['status'] in ('stopping', 'stopped')
        assert wait_wsl_dead(trainer_pid), 'the trainer must be gone'
        assert wait_wsl_dead(int(grandchild.read_text().strip())), 'the grandchild must be gone'
        assert wait_wsl_dead(pgid), 'nothing may be left in the training process group'
        assert wait_for(lambda: process.poll() is not None, 20) is not None or \
            process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
        subprocess.run([control.wsl, '-d', control.distro, '-e', 'rm', '-rf', state],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    final = control.get()
    assert final['status'] in ('stopped', 'interrupted')
    assert final['adapter_published'] is False


def _trainer_pid(tmp_path):
    status = tmp_path/'training-status.json'
    if not status.exists():
        return None
    return json.loads(status.read_text()).get('trainer_pid')


def _posix(path):
    """A workspace path spelled the way WSL sees it (`/mnt/c/Code/...`)."""
    text = str(path).replace('\\', '/')
    drive, _, rest = text.partition(':')
    return f'/mnt/{drive.lower()}{rest}' if rest else text


def test_group_kill_signal_runs_inside_wsl_and_is_idempotent(tmp_path):
    """The kill is delegated to WSL, and a group that no longer exists is success.

    The holder and its victim are launched inside WSL, so the process group id in the
    pid file is a WSL group id -- the same thing the worker registers in production.
    Stop must then empty that group from the Windows side.
    """
    if os.name == 'nt' and not _wsl_available():
        pytest.skip('no WSL available to host a process group')
    argv = _wsl_signal_holder(tmp_path)
    holder = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              text=True, stderr=subprocess.STDOUT,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    line = holder.stdout.readline().strip()
    if not line.startswith('PGID'):
        holder.kill()
        pytest.skip(f'the WSL holder did not start: {line!r}')
    pgid, victim = (int(x) for x in line.split()[1:3])
    control = TrainingControl(tmp_path/'projection', python=WSL_PYTHON, state=_posix(tmp_path/'lab'),
                              launch=False, log=tmp_path/'control.log')
    (tmp_path/'projection').mkdir(parents=True, exist_ok=True)
    (tmp_path/'projection'/'training-status.json').write_text(json.dumps(
        {'run_id': 'live', 'status': 'running', 'updated_at': time.time(),
         'pid': pgid, 'trainer_pid': victim}))
    (tmp_path/'projection'/'training.pid').write_text(json.dumps(
        {'run_id': 'live', 'pgid': pgid, 'pid': pgid}))
    control.process = SimpleNamespace(poll=lambda: None)
    state = control('stop', {})
    assert state['status'] == 'stopped', state
    assert wait_wsl_dead(pgid) and wait_wsl_dead(victim), 'the whole WSL group must be gone'
    control.process = None
    assert control('stop', {})['status'] == 'stopped'      # nothing running: still no error
    holder.kill()


def _wsl_available():
    try:
        return subprocess.run(['wsl.exe', '-d', 'Ubuntu', '-e', 'true'], timeout=60,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                              ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wsl_signal_holder(tmp_path):
    """Print `PGID <group> <victim>` from inside WSL, then sleep with both alive."""
    script = (
        'import subprocess, sys, time, os\n'
        'victim = subprocess.Popen(["sleep", "600"])\n'
        'print("PGID", os.getpgrp(), victim.pid, flush=True)\n'
        'time.sleep(600)\n')
    path = tmp_path/'holder.py'
    path.write_text(script, encoding='utf-8')
    return ['wsl.exe', '-d', 'Ubuntu', '-e', WSL_PYTHON, _posix(path)]


def test_stop_without_any_run_is_a_no_op(tmp_path):
    control = TrainingControl(tmp_path, launch=False)
    assert control('stop', {})['status'] == 'off'
    assert control.get()['active'] is False
    assert not (tmp_path/'training-stop.json').exists()


def test_stop_targets_the_adopted_run_id_and_is_idempotent(tmp_path):
    control = TrainingControl(tmp_path, launch=False)
    (tmp_path/'training-status.json').write_text(json.dumps(
        {'run_id': 'adopted', 'status': 'running', 'updated_at': time.time()}))
    control('stop', {})
    assert json.loads((tmp_path/'training-stop.json').read_text())['run_id'] == 'adopted'
    control('stop', {})
    assert json.loads((tmp_path/'training-stop.json').read_text())['run_id'] == 'adopted'


# --- 2. Only a clean stop WITH a publish marker publishes -----------------------
def test_a_clean_stop_that_published_nothing_is_failed_not_completed(tmp_path):
    """The trainer's own no-op gate: 3 steps, no tensor changed, so nothing is published."""
    script = write_trainer(tmp_path/'work')
    control = controller(tmp_path)
    plan, process = launch(control, script, run_id='noop', env={'DUMMY_PUBLISH': '0'})
    allow(control)
    process.wait(timeout=60)
    state = wait_terminal(control)
    assert state['status'] == 'failed', state
    assert state['adapter_published'] is False and state['published_adapter'] is None
    assert 'no weight changed' in (state['terminal_reason'] or '')
    assert not (control.directory/'training-receipt.json').exists() or \
        not control.get()['adapter_published']


def test_clean_exit_that_wrote_no_receipt_is_failed_not_completed(tmp_path):
    """Exit 0 alone is not a completed run: the receipt and marker are what decide."""
    script = write_trainer(tmp_path/'work')
    control = controller(tmp_path)
    plan, process = launch(control, script, run_id='noreceipt', env={'DUMMY_NORECEIPT': '1'})
    allow(control)
    process.wait(timeout=60)
    state = wait_terminal(control)
    assert state['status'] == 'failed', state
    assert state['adapter_published'] is False and state['published_adapter'] is None
    assert 'no receipt' in (state['terminal_reason'] or '')


def test_published_receipt_and_marker_complete_and_name_the_adapter(tmp_path):
    script = write_trainer(tmp_path/'work')
    control = controller(tmp_path)
    plan, process = launch(control, script, run_id='ok')
    allow(control)
    process.wait(timeout=60)
    state = wait_terminal(control)
    assert state['status'] == 'completed', state
    assert state['adapter_published'] is True
    adapter = Path(state['published_adapter'])
    assert (adapter/'adapter_model.safetensors').exists()
    assert state['steps'] == 3, 'progress must come from the trainer step lines'
    assert state['examples_used'] == 3, "the trainer's own receipt supplies the count"
    assert state['peak_gpu_gb'] == 0.5
    assert state['stop_reason'] == 'step budget'
    assert state['tensors_changed'] == 4
    assert 'publish marker' in (state['terminal_reason'] or '')


def test_a_receipt_claiming_publication_without_its_marker_is_failed(tmp_path):
    """The marker is the artifact; a receipt alone cannot publish one."""
    script = write_trainer(tmp_path/'work')
    control = controller(tmp_path)
    plan, process = launch(control, script, run_id='nomarker',
                           env={'DUMMY_MARKER_MISSING': '1'})
    allow(control)
    process.wait(timeout=60)
    state = wait_terminal(control)
    assert state['status'] == 'failed', state
    assert state['adapter_published'] is False
    assert 'no publish marker' in (state['terminal_reason'] or '')


def test_an_already_published_directory_is_terminal_not_retryable(tmp_path):
    """The trainer refuses to overwrite a published adapter; the panel must not retry."""
    script = write_trainer(tmp_path/'work')
    control = controller(tmp_path)

    def publish_earlier(plan):
        # `native(...)` because this worker is the Windows interpreter: `launch` converts every
        # argv token the same way, so the `--out` the worker really uses is the native spelling.
        # Writing to the raw `/mnt/...` string instead lands in `C:\mnt\...`, where the worker
        # does not look -- which made this test silently assert the wrong thing: no marker was
        # found, so the run trained and finished 'completed' instead of refusing to overwrite.
        published = Path(native(plan['out']))
        published.mkdir(parents=True, exist_ok=True)
        (published/PUBLISH_MARKER).write_text('{"steps": 7}')

    plan, process = launch(control, script, run_id='again', before_start=publish_earlier)
    allow(control)
    process.wait(timeout=60)
    state = wait_terminal(control)
    assert state['status'] == 'already_published', state
    assert state['active'] is False
    assert state['adapter_published'] is False, 'nothing new was published by this attempt'
    assert 'already published' in (state['terminal_reason'] or '')


def test_interrupted_run_reports_interrupted_and_publishes_no_adapter(tmp_path):
    """A stale heartbeat means the worker vanished; nothing may be claimed."""
    control = TrainingControl(tmp_path, launch=False)
    live = {'run_id': 'gone', 'steps': 7, 'max_steps': 20, 'examples_used': 33,
            'adapter_published': True, 'adapter': str(tmp_path/'adapters')}
    (tmp_path/'training-status.json').write_text(json.dumps(
        {**live, 'status': 'running', 'updated_at': time.time()}))
    assert control.get()['status'] == 'running'
    (tmp_path/'training-status.json').write_text(json.dumps(
        {**live, 'status': 'running', 'updated_at': time.time()-3600}))
    state = control.get()
    assert state['status'] == 'interrupted'
    assert state['active'] is False and state['stop_pending'] is False
    assert state['adapter_published'] is False and state['published_adapter'] is None
    assert not (tmp_path/'training-receipt.json').exists()
    assert state['steps'] == 7, 'an interrupted run still reports what it reached'


def test_stopped_run_publishes_no_adapter_even_with_an_adapter_left_on_disk(tmp_path):
    """A trainer may leave artifacts behind; a stopped run still publishes nothing."""
    control = TrainingControl(tmp_path, launch=False)
    out = tmp_path/'adapters'
    (out/'adapter_model.safetensors').parent.mkdir(parents=True, exist_ok=True)
    (out/'adapter_model.safetensors').write_text('weights')
    (out/PUBLISH_MARKER).write_text('{"steps": 3}')
    (tmp_path/'training-status.json').write_text(json.dumps(
        {'run_id': 'cut', 'status': 'stopped', 'updated_at': time.time(), 'steps': 3,
         'max_steps': 20, 'terminal_reason': 'stopped by the user',
         'adapter': str(out), 'adapter_published': True}))
    (tmp_path/'training-receipt.json').write_text(json.dumps(
        {'run_id': 'cut', 'status': 'stopped', 'published': True, 'out': str(out)}))
    state = control.get()
    assert state['status'] == 'stopped'
    assert state['adapter_published'] is False and state['published_adapter'] is None
    assert state['terminal_reason'] == 'stopped by the user'


def test_a_completed_status_without_this_runs_receipt_is_failed(tmp_path):
    """`completed` is a claim about a written receipt; without one it cannot stand.

    The trainer writes the receipt, so a projection that says `completed` while no
    receipt exists for this run is an unverifiable claim -- and the adapter it names
    must not be published.
    """
    control = TrainingControl(tmp_path, launch=False)
    (tmp_path/'training-status.json').write_text(json.dumps(
        {'run_id': 'this', 'status': 'completed', 'updated_at': time.time(),
         'adapter_published': True, 'adapter': '/home/grant/decomp/old'}))
    state = control.get()
    assert state['run_id'] == 'this'
    assert state['status'] == 'failed'
    assert state['adapter_published'] is False and state['published_adapter'] is None
    (tmp_path/'training-receipt.json').write_text(json.dumps(
        {'run_id': 'other', 'status': 'completed', 'adapter_published': True,
         'adapter': '/home/grant/decomp/old'}))
    state = control.get()
    assert state['run_id'] == 'this'
    assert state['adapter_published'] is False, "another run's receipt cannot publish"


# --- 3. Restart adopts, never resets -------------------------------------------
def test_restarting_the_control_plane_does_not_reset_a_live_runs_counters(tmp_path):
    first = TrainingControl(tmp_path)
    (tmp_path/'training-status.json').write_text(json.dumps(
        {'run_id': 'live', 'status': 'running', 'updated_at': time.time(), 'steps': 41,
         'max_steps': 60, 'examples_used': 512, 'max_examples': 2048,
         'elapsed_seconds': 900.0, 'minutes': 60}))
    before = first.get()
    assert before['active'] and before['steps'] == 41 and before['examples_used'] == 512
    with pytest.raises(RuntimeError, match='already'):
        first('start', {})                    # a live run cannot be restarted over
    fresh = TrainingControl(tmp_path)          # the dashboard process restarts
    adopted = fresh.get()
    assert adopted['run_id'] == 'live' and adopted['steps'] == 41
    assert adopted['examples_used'] == 512 and adopted['elapsed_seconds'] == 900.0
    assert adopted['max_steps'] == 60 and adopted['max_examples'] == 2048
    assert adopted['active'] is True
    (tmp_path/'training-stop.json').write_text(json.dumps({'run_id': 'live'}))
    assert fresh.get()['status'] == 'stopping'  # and the adopted run can still be stopped


# --- 4. Read-only disables the training controls --------------------------------
def test_read_only_disables_the_training_controls(tmp_path):
    lab = TrainingControl(tmp_path, enabled=False, launch=False)
    with pytest.raises(ValueError, match='read-only'):
        lab('start', {})
    with pytest.raises(ValueError, match='read-only'):
        lab('stop', {})
    assert lab.get()['controls_enabled'] is False


def test_read_only_http_routes_refuse_training_control(tmp_path):
    def unavailable():
        raise ValueError('No campaign data')
    lab = TrainingControl(tmp_path, enabled=False, launch=False)
    http = ThreadingHTTPServer(('127.0.0.1', 0), handler(SimpleNamespace(get=unavailable), None,
                                                         None, lab))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{http.server_port}'
        with urlopen(base+'/api/training') as response:
            assert json.load(response)['controls_enabled'] is False
        request = Request(base+'/api/training/control', data=b'{"action":"start"}',
                          headers={'X-Campaign-Control': 'ui', 'Content-Type': 'application/json'})
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
        lab.enabled = True
        with urlopen(Request(base+'/api/training/control', data=b'{"action":"stop"}',
                             headers={'X-Campaign-Control': 'ui'})) as response:
            assert json.load(response)['status'] == 'off'
        with urlopen(base+'/') as response:
            assert b'progress_training.js' in response.read()
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


# --- 5. The launch contract, limits, and refusals -------------------------------
def test_start_uses_the_documented_entry_point_and_limits(tmp_path):
    control = TrainingControl(tmp_path, python=WSL_PYTHON,
                              state='/home/grant/decomp/local-research', launch=False)
    plan = control('start', {'minutes': 30, 'max_steps': 100, 'max_examples': 500})
    trainer = plan['trainer']
    assert trainer[:3] == [WSL_PYTHON, '-m', TRAINER_MODULE]
    for flag, value in [('--base', '/home/grant/decomp/models/qwen2.5-coder-7b'),
                        ('--tasks', '/home/grant/decomp/local-research/training/tasks.jsonl'),
                        ('--split', 'train'), ('--max-seq-len', '2304'), ('--max-steps', '100'),
                        ('--max-seconds', '1800'), ('--max-examples', '500'), ('--block-size', '4')]:
        assert trainer[trainer.index(flag)+1] == value, flag
    assert plan['out'].startswith('/home/grant/decomp/local-research/training/adapters/')
    assert plan['receipt'] == f"{plan['out']}/{TRAINER_RECEIPT}"
    assert plan['marker'] == f"{plan['out']}/{PUBLISH_MARKER}"
    assert plan['worker'][plan['worker'].index('--run-id')+1] == plan['run_id']


def test_tasks_is_the_trainers_flag_and_dataset_is_accepted_as_its_alias(tmp_path):
    control = TrainingControl(tmp_path, state='/home/grant/decomp/local-research', launch=False)
    for options in ({'tasks': '/data/a.jsonl'}, {'dataset': '/data/a.jsonl'}):
        plan = control('start', options)
        assert plan['trainer'][plan['trainer'].index('--tasks')+1] == '/data/a.jsonl'
        assert '--dataset' not in plan['trainer']


def test_defaults_and_hard_ceilings_are_the_documented_ones():
    assert (DEFAULT_STEPS, DEFAULT_MINUTES, DEFAULT_EXAMPLES) == (600, 60, 20000)
    assert (MAX_STEPS, MAX_MINUTES, MAX_EXAMPLES) == (20000, 480, 200000)
    assert (DEFAULT_SEQ_LEN, DEFAULT_BLOCK_SIZE) == (2304, 4)
    assert (MAX_SEQ_LEN, ) == (16384,)
    assert REQUIRED_TRAINER_FLAGS == ('--base', '--tasks', '--out', '--max-steps',
                                      '--max-examples', '--max-seconds')
    assert TRAINER_MODULE == 'eval.train_source_repair'


# --- 6. The real launch path, through wsl.exe ----------------------------------
def test_launching_through_the_real_path_reaches_running_and_stops_cleanly(tmp_path):
    """The control plane's own launch, not a rewired argv: WSL worker, status, Stop.

    A stand-in trainer module is installed inside the repo's own `eval` package for
    the duration of the test, so the probe, the interpreter, the working directory
    and the projection are all the ones a real run uses.
    """
    if not _wsl_available():
        pytest.skip('no WSL available to launch a real worker')
    stub = ROOT/'eval'/'train_source_repair_probe.py'
    stub.write_text(DUMMY, encoding='utf-8')
    state = f'/home/grant/decomp/training-e2e-{os.getpid()}-{int(time.time())}'
    control = TrainingControl(tmp_path/'projection', python=WSL_PYTHON, state=state,
                              trainer_module='eval.train_source_repair_probe',
                              log=tmp_path/'control.log')
    (tmp_path/'projection').mkdir(parents=True, exist_ok=True)
    previous = {name: os.environ.get(name) for name in ('WSLENV', 'TRAIN_PROBE_ALLOW')}
    # WSL does not inherit a Windows-side environment: only WSLENV-listed names cross.
    os.environ['WSLENV'] = 'TRAIN_PROBE_ALLOW'
    os.environ['TRAIN_PROBE_ALLOW'] = _posix(tmp_path/'projection'/'allow')
    subprocess.run(['wsl.exe', '-d', 'Ubuntu', '-e', 'sh', '-c',
                    f'mkdir -p {state} && printf \'{{"id":"t1","split":"train"}}\\n\' > '
                    f'{state}/tasks.jsonl'],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        plan = control('start', {'minutes': 5, 'max_steps': 50, 'max_examples': 8,
                                 'tasks': f'{state}/tasks.jsonl'})
        assert plan['active'], plan
        (tmp_path/'projection'/'allow').write_text('')
        live = wait_for(lambda: control.get().get('trainer_pid'), 90)
        if not live:
            log = control.directory/f'training-worker-{plan["run_id"]}.log'
            pytest.fail('the real WSL launch did not start: '
                        + (log.read_text(errors='replace')[-600:] if log.exists()
                           else f'no worker log at {log}'))
        assert control.get()['status'] == 'running'
        stopped = control('stop', {})
        assert stopped['status'] in ('stopped', 'stopping'), stopped
        assert wait_for(lambda: control.get()['status'] == 'stopped', 60), control.get()
        final = control.get()
        assert final['adapter_published'] is False and final['published_adapter'] is None
        assert wait_wsl_dead(final['trainer_pid']), 'Stop must end the trainer process group'
        assert control('stop', {})['status'] == 'stopped'      # idempotent
    finally:
        for name, value in previous.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        stub.unlink(missing_ok=True)
        subprocess.run(['wsl.exe', '-d', 'Ubuntu', '-e', 'rm', '-rf', state],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


@pytest.mark.parametrize('options', [{'minutes': 0}, {'minutes': 481}, {'max_steps': 0},
    {'max_steps': 20001}, {'max_examples': 200001}, {'max_steps': True}, {'minutes': '30'},
    {'max_seq_len': 0}, {'max_seq_len': 16385}, {'block_size': 0}, {'block_size': 65},
    {'base': 'x\ny'}, {'tasks': 'C:\\data\\training.jsonl'}, {'base': 'C:\\models\\qwen'},
    {'endpoint': 'https://paid.example'}, {'repo': '/tmp/other'}, {'max_calls': 4}])
def test_bad_training_options_never_launch_a_worker(tmp_path, options, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail('An invalid request launched a process')
    monkeypatch.setattr('eval.training_control.subprocess.Popen', forbidden)
    with pytest.raises(ValueError):
        TrainingControl(tmp_path)('start', options)


def test_group_kill_command_is_an_argv_that_names_the_group():
    argv = group_kill_command('Ubuntu', 4321, 'TERM', python='/usr/bin/python3')
    assert argv == ['kill', '-d', 'Ubuntu', '-e', '/usr/bin/python3',
                    '-m', 'eval.training_control', '--signal', 'TERM', '--pgid', '4321']


def test_worker_refuses_to_claim_a_limit_the_trainer_cannot_enforce(tmp_path):
    """A flag the trainer does not advertise aborts the run instead of being invented."""
    script = tmp_path/'thin_trainer.py'
    script.write_text('import argparse\n'
                      'ap = argparse.ArgumentParser()\n'
                      'ap.add_argument("--out")\n'
                      'ap.add_argument("--tasks")\n'
                      'ap.add_argument("--max-steps")\n'
                      'ap.parse_args()\n', encoding='utf-8')
    control = controller(tmp_path)
    plan, argv, environment = wire(control, script, run_id='thin')
    process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               env=environment,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    out, _ = process.communicate(timeout=60)
    assert process.returncode == 2
    assert '--max-examples' in out and '--base' in out
    assert 'could not be probed' not in out, 'the interpreter ran; this is a flag finding'
    state = wait_terminal(control)
    assert state['status'] == 'failed'
    assert state['adapter_published'] is False


# --- 7. A probe that cannot run is not an empty flag set ------------------------
def test_a_nonexistent_interpreter_is_reported_as_an_interpreter_error(tmp_path):
    """`python` does not exist in this WSL install; that must never read as "no flags"."""
    probe, interpreter = trainer_flags('/nonexistent/python', repo_for_worker())
    assert interpreter is None
    assert probe.ok is False
    assert probe.flags is None, 'a probe that never ran cannot report an empty flag set'
    assert '/nonexistent/python' in (probe.error or '')
    assert REQUIRED_TRAINER_FLAGS == tuple(probe.missing)
    assert probe.tried == ['/nonexistent/python']


def test_the_launch_reports_an_interpreter_error_not_missing_trainer_flags(tmp_path):
    """The end-to-end form of the same rule, through the worker's own CLI."""
    script = write_trainer(tmp_path/'work')
    control = controller(tmp_path)
    plan, argv, environment = wire(control, script, run_id='nointerp')
    argv[argv.index('--trainer-python')+1] = '/nonexistent/python'
    argv[argv.index('--trainer')+1] = '/nonexistent/python'
    process = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               env=environment,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    out, _ = process.communicate(timeout=60)
    assert process.returncode == 2
    payload = json.loads(out.strip().splitlines()[-1])
    assert 'trainer_probe_error' in payload, out
    assert 'missing_trainer_flags' not in payload, 'an unrun probe must not report flags'
    assert 'could not be executed' in payload['trainer_probe_error']
    state = wait_terminal(control)
    assert state['status'] == 'failed'
    assert 'could not be probed' in (state['terminal_reason'] or '')
    assert 'does not offer' not in (state['terminal_reason'] or '')


def test_an_opt_in_interpreter_fallback_is_used_and_recorded(tmp_path):
    """A fallback is never automatic: it is declared, tried second, and reported."""
    script = write_trainer(tmp_path/'work')
    probe, interpreter = trainer_flags('/nonexistent/python', repo_for_worker(),
                                       module=str(Path(script).resolve()),
                                       fallbacks=[sys.executable])
    assert probe.ok and interpreter == sys.executable
    assert probe.tried == ['/nonexistent/python', sys.executable]
    assert set(REQUIRED_TRAINER_FLAGS) <= probe.flags
    assert probe.missing == []
    # And the default is no fallback at all.
    plain, none = trainer_flags('/nonexistent/python', repo_for_worker(),
                                module=str(Path(script).resolve()))
    assert none is None and plain.ok is False
