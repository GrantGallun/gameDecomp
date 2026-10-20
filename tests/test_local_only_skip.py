"""The conftest skip for gitignored inputs fires on them and on nothing else."""

import pytest

import conftest


def missing(path):
    return FileNotFoundError(2, "No such file or directory", str(path))


@pytest.mark.parametrize("relative", ["tests/fixtures/register_storage.c",
                                      "eval/results/swarm-final/a.c",
                                      ".cache/recon/serve_lowfootprint.sh"])
def test_a_missing_local_only_input_skips_and_names_the_file(relative):
    with pytest.raises(pytest.skip.Exception, match=f"local-only input absent: {relative}"):
        conftest._skip_if_local_only(missing(conftest.ROOT / relative))


@pytest.mark.parametrize("path", ["solver/recorded_layout.py",       # tracked source
                                  "tests/fixtures_elsewhere/x.c",    # prefix, not a child
                                  "eval/resultsx/y.json"])
def test_a_missing_file_outside_the_local_only_roots_still_fails(path):
    assert conftest._skip_if_local_only(missing(conftest.ROOT / path)) is None


def test_an_error_without_a_filename_still_fails():
    assert conftest._skip_if_local_only(FileNotFoundError("no path")) is None
