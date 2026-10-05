"""Amendment tests, run against the FROZEN project with the staged fast_runtime installed there."""
import json
import os
from pathlib import Path
import sys

import pytest

PROJECT = Path(os.environ["AMENDMENT_PROJECT"])
sys.path.insert(0, str(PROJECT))

from eval import fast_runtime  # noqa: E402

assert Path(fast_runtime.__file__).resolve() == (PROJECT / "eval/fast_runtime.py").resolve()


@pytest.fixture(autouse=True)
def scoped_runtime_wrappers():
    if fast_runtime._active is not None:
        fast_runtime._active.close()
    yield
    if fast_runtime._active is not None:
        fast_runtime._active.close()


def test_build_artifacts_are_only_what_this_build_wrote(tmp_path):
    import time
    source = tmp_path / 'fn.c'; source.write_text('int f(void){return 1;}')
    (tmp_path / 'fn_agentrepair_retained_1_diff').write_text('debris from another candidate')
    (tmp_path / 'fn.o').write_bytes(b'stale object')
    before = fast_runtime._artifact_stamps(tmp_path, source)
    time.sleep(0.02)
    (tmp_path / 'fn.o').unlink(); (tmp_path / 'fn.o').write_bytes(b'new object')
    (tmp_path / 'fn_object_dump_normalized.s').write_text('asm')
    assert fast_runtime.built_artifacts(tmp_path, source, before) == ['fn.o', 'fn_object_dump_normalized.s']


def test_compile_cache_excludes_debris_and_replays_old_hex_entries(tmp_path, monkeypatch):
    from solver import workspace
    calls = {'build': 0}
    ws = tmp_path / 'ws'; ws.mkdir()
    (ws / 'candidate.c').write_text('int f(void){return 1;}')
    (ws / 'candidate_agentrepair_retained_1_diff').write_text('x' * 100000)
    script = ws / 'build.sh'; script.write_text('build')

    def shell(cmd, cwd=None, timeout=300):
        calls['build'] += 1
        (cwd / 'candidate.o').write_bytes(b'object')
        return 0, 'score'
    monkeypatch.setattr(workspace, 'sh', shell)
    fast_runtime.install(tmp_path / 'cache', 'pin1', tmp_path / 'model.lock')
    cmd = f'. /fake/activate && bash {script} candidate.c'
    workspace.sh(cmd, cwd=ws)
    [value] = list((tmp_path / 'cache' / 'compile').glob('*/*/value.json'))
    record = json.loads(value.read_text())['value']
    assert sorted(record['artifacts']) == ['candidate.o'] and record['encoding'] == fast_runtime.ARTIFACT_ENCODING
    old = {'returncode': 0, 'stdout': 'score', 'artifacts': {'candidate.o': b'old object'.hex()}}
    value.write_text(json.dumps({'sha256': fast_runtime.key(old), 'value': old}))
    (ws / 'candidate.o').unlink()
    workspace.sh(cmd, cwd=ws)
    assert (ws / 'candidate.o').read_bytes() == b'old object' and calls['build'] == 1
