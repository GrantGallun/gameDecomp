"""Fresh, muted, bounded Project64 capture worker. Windows CLI; no UI automation.

Call from WSL using Windows Python and a JSON job with Windows absolute paths.
The worker never reuses captures, changes the source bundle, or stops an existing
emulator. Only its own Popen process handle may be terminated during cleanup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from solver import project64_capture, runtime_capture

ASSETS_SHA256 = '4fbe1bbd083f99dd9b67f5cf3e8aff0c13cc4326b7c8e527537a537b13387a61'
TEMPLATE_SHA256 = '5282a2fa0ea7215a783a8219edcab68f9c4faeca7181a31bcce226c3b5941a29'
EXE_SHA256 = 'f8b954eaa879da5f5fca2fce7ed5081a969eef4acc46e306b26f81709de8bfe7'
CONFIG = '''[Settings]
Force Interpreter CPU=1
Current Language=English
Basic Mode=0
Auto Start=1

[Debugger]
Debugger=1
Autorun Scripts=capture-entry.js

[Plugin]
Graphics Dll=GFX\\GLideN64\\GLideN64.dll
Graphics Dll Default=GFX\\GLideN64\\GLideN64.dll
Audio Dll=Audio\\Project64-Audio.dll
RSP Dll=RSP\\Project64-RSP.dll
Controller Dll=Input\\Project64-Input.dll

[Audio-Settings]
Volume=0

[Logging]
Log Auto Flush=1
App Init=5
N64 System=5
Plugins=5
'''


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _is_windows():
    return os.name == 'nt'


def _write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def _read(path):
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('capture JSON exceeds bounded size')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _existing_emulators():
    # Get-Process uses the process inventory without tasklist's WMI dependency.
    # This constant command inspects no UI and never terminates a process.
    command = ("$ErrorActionPreference='Stop'; Get-Process | "
               "Where-Object { $_.ProcessName -eq 'Project64' } | ForEach-Object { $_.Id }")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=10, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    rows = result.stdout.split()
    if any(not row.isdigit() for row in rows):
        raise ValueError('unexpected emulator process inventory response')
    return rows


def _launch(exe, rom):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return subprocess.Popen([str(exe), str(rom)], cwd=exe.parent, startupinfo=startup,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


def _validate(job):
    if job.get('schema_version') != 1:
        raise ValueError('unsupported capture job schema')
    if job.get('selector') not in {'first', 'a0_nonzero'}:
        raise ValueError('unsupported entry selector')
    timeout = job.get('timeout_seconds')
    if type(timeout) not in {int, float} or not 5 <= timeout <= 120:
        raise ValueError('capture timeout must be between 5 and 120 seconds')
    plan = job.get('plan', {})
    windows = runtime_capture.validate_plan(plan)
    if plan['registers'] != project64_capture.register_map():
        raise ValueError('capture job must preserve full physical register widths')
    if sum(row['size'] for row in windows) > 512 * 1024:
        raise ValueError('automatic script capture is bounded to 512 KiB RAM')
    if plan['ram'] != windows:
        raise ValueError('capture RAM plan must be in address order')
    for key in ('portable_dir', 'rom_path', 'output_dir'):
        if not isinstance(job.get(key), str) or not Path(job[key]).is_absolute():
            raise ValueError(key + ' must be an absolute Windows path')
    return plan


def _prepare(job, output):
    plan = _validate(job)
    source = Path(job['portable_dir']).resolve(strict=True)
    if output.is_relative_to(source):
        raise ValueError('capture output must be outside the source portable bundle')
    assets_data = Path(__file__).with_name('project64_assets.json').read_bytes()
    template = Path(__file__).with_name('project64_capture_entry.js').read_bytes()
    if _sha(assets_data) != ASSETS_SHA256 or _sha(template) != TEMPLATE_SHA256:
        raise ValueError('capture asset manifest or exporter template differs from audited revision')
    assets = json.loads(assets_data)
    if assets.get('Project64.exe') != EXE_SHA256:
        raise ValueError('capture executable manifest identity differs')
    portable = output / 'portable'
    portable.mkdir()
    for name, expected in assets.items():
        path = (source / name).resolve(strict=True)
        if not path.is_relative_to(source):
            raise ValueError('portable asset escapes source bundle')
        data = path.read_bytes()
        if _sha(data) != expected:
            raise ValueError('portable asset differs from approved revision: ' + name)
        target = portable / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(data)
    original_rom = Path(job['rom_path']).resolve(strict=True)
    rom_data = original_rom.read_bytes()
    if _sha(rom_data) != plan['rom_sha256'] or rom_data[:4] != bytes.fromhex('80371240'):
        raise ValueError('capture ROM differs from retained plan identity')
    rom = output / 'input.z64'
    with rom.open('xb') as stream:
        stream.write(rom_data)
    _write(output / 'capture-plan.json', plan)
    (portable / 'Config' / 'Project64.cfg').write_text(CONFIG, encoding='ascii')
    (portable / 'Scripts').mkdir()
    prefix = 'var captureJob = ' + json.dumps({'plan': plan, 'selector': job['selector'],
               'outputRoot': output.as_posix() + '/'}, ensure_ascii=True) + ';\n'
    script = prefix.encode('ascii') + template
    (portable / 'Scripts' / 'capture-entry.js').write_bytes(script)
    _write(output / 'launch-inputs.json', {'assets_sha256': ASSETS_SHA256,
        'template_sha256': TEMPLATE_SHA256, 'script_sha256': _sha(script),
        'exe_sha256': EXE_SHA256, 'rom_sha256': plan['rom_sha256'],
        'config_sha256': _sha((portable / 'Config' / 'Project64.cfg').read_bytes()),
        'muted_volume': 0, 'source_portable': str(source), 'source_rom': str(original_rom)})
    return portable / 'Project64.exe', rom


def _collect(process, output, deadline):
    status = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return 'emulator_exited', status
        path = output / 'bridge-status.json'
        if path.exists():
            try:
                status = _read(path)
            except (json.JSONDecodeError, PermissionError):
                time.sleep(0.05)
                continue
            if status.get('stage') == 'captured':
                return 'captured', status
            if status.get('stage') in {'capture_error', 'registration_error'}:
                return 'export_error', status
        time.sleep(0.1)
    return 'timeout', status


def run_job(job):
    """Run one fresh Windows capture; every admitted output has a final receipt."""
    output = Path(job['output_dir'])
    if not output.is_absolute():
        raise ValueError('output_dir must be absolute')
    output.mkdir(parents=True, exist_ok=False)
    output = output.resolve()
    receipt = {'schema_version': 1, 'kind': 'project64-capture-run', 'status': 'error',
               'output_dir': str(output), 'started_unix': time.time(), 'muted': True,
               'capture_path': None, 'raw_path': None, 'plan_path': None,
               'cleanup': {'owned_pid': None, 'terminated': False, 'exited': True}}
    process = None
    started = time.monotonic()
    try:
        _write(output / 'job.json', job)
        if not _is_windows():
            raise ValueError('capture launcher requires Windows Python; invoke it from WSL via python.exe')
        _validate(job)
        existing = _existing_emulators()
        if existing:
            receipt.update(status='blocked_existing_emulator', existing_pids=existing)
            return receipt
        exe, rom = _prepare(job, output)
        deadline = started + job['timeout_seconds']
        if time.monotonic() >= deadline:
            receipt['status'] = 'timeout_preparing'
            return receipt
        process = _launch(exe, rom)
        receipt['cleanup'] = {'owned_pid': process.pid, 'terminated': False, 'exited': False}
        _write(output / 'launch.json', {'pid': process.pid, 'exe': str(exe), 'rom': str(rom),
                                      'started_unix': time.time(), 'muted_volume': 0})
        receipt['status'], receipt['bridge'] = _collect(process, output, deadline)
        if receipt['status'] == 'captured':
            raw_path = output / 'project64-entry-raw.json'
            raw = _read(raw_path)
            # Check selector on the captured state too, not only in the exporter.
            if job['selector'] == 'a0_nonzero' and raw['samples'][0]['gpr'][4] == 0:
                raise ValueError('captured entry does not satisfy nonzero selector')
            record = project64_capture.import_export(raw, job['plan'], rom)
            _write(output / 'capture.json', record)
            receipt.update(capture_path=str(output / 'capture.json'), raw_path=str(raw_path),
                           plan_path=str(output / 'capture-plan.json'),
                           capture_sha256=record['sha256'], raw_file_sha256=_sha(raw_path.read_bytes()))
    except Exception as error:
        receipt.update(status='error', error=f'{type(error).__name__}: {error}')
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    receipt['cleanup']['terminated'] = True
                process.wait(timeout=5)
                receipt['cleanup'].update(exited=True, exit_code=process.returncode)
            except Exception as error:
                receipt['cleanup']['error'] = f'{type(error).__name__}: {error}'
                receipt['status'] = 'cleanup_error'
        receipt['elapsed_seconds'] = time.monotonic() - started
        _write(output / 'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    try:
        receipt = run_job(_read(Path(args.job)))
    except Exception as error:
        receipt = {'schema_version': 1, 'status': 'error', 'error': f'{type(error).__name__}: {error}'}
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
