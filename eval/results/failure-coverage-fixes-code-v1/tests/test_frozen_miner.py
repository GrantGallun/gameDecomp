from eval import frozen_wavefront
import pytest


def test_miner_implementation_is_pinned_with_solver(tmp_path):
    directory=tmp_path/'miner'
    directory.mkdir()
    path=directory/'evidence.py'
    path.write_text('version = 1')
    pins=frozen_wavefront.file_hashes(frozen_wavefront.code_paths(tmp_path))
    assert str(path.resolve()) in pins
    path.write_text('version = 2')
    with pytest.raises(frozen_wavefront.FrozenInputChanged):
        frozen_wavefront.verify_files(pins)
