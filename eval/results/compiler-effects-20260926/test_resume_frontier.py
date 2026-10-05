import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    'resume_frontier_bounded', Path(__file__).with_name('resume_frontier.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_and_valid_limits():
    assert MODULE.bounded_args(['--run']).max_batches == 5
    assert MODULE.bounded_args(['--run', '--max-batches', '1']).max_batches == 1
    assert MODULE.bounded_args(['--run', '--batch', '5']).batch == 5


@pytest.mark.parametrize('args', [
    ['--max-batches', '6'], ['--max-batches', '0'],
    ['--batch', '6'], ['--batch', '0'],
])
def test_out_of_bound_invocation_rejected(args):
    with pytest.raises(SystemExit) as exc:
        MODULE.bounded_args(args)
    assert exc.value.code == 2
