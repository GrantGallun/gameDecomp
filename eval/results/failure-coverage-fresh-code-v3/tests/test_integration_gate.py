import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import integration_gate as gate


def test_rom_comparison_checks_entire_image_and_size():
    assert gate.compare_roms(b"abc", b"abc")["whole_rom_verified"]
    assert gate.compare_roms(b"abc", b"abx")["first_difference_offset"] == 2
    assert gate.compare_roms(b"abc", b"abcd")["first_difference_offset"] == 3
    assert not gate.compare_roms(b"", b"")["whole_rom_verified"]


@pytest.mark.parametrize("name", ["../outside.c", "/absolute.c", "."])
def test_paths_cannot_escape(tmp_path, name):
    with pytest.raises(ValueError):
        gate.inside(tmp_path, name)


@pytest.mark.parametrize("success", [True, False])
def test_isolated_build_keeps_original_and_never_claims_all_c(tmp_path, monkeypatch, success):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    original = repo / "src/f.c"
    original.write_bytes(b"INCLUDE_ASM(f);\n")
    (repo / "base.z64").write_bytes(b"ROM")
    replacement = tmp_path / "new.c"
    replacement.write_bytes(b"int f(void) { return 1; }\n")
    manifest = tmp_path / "integration.json"
    manifest.write_text(json.dumps({
        "reference_rom": "base.z64", "reference_sha256": gate.sha(b"ROM"),
        "built_rom": "build/game.z64", "replacements": [{
            "path": "src/f.c", "base_sha256": gate.sha(original.read_bytes()),
            "replacement": "new.c", "replacement_sha256": gate.sha(replacement.read_bytes())}]}))

    def build(argv, *, cwd, **kwargs):
        assert argv == ["bash", "./tools/build-and-verify.sh"]
        assert cwd != repo
        assert (cwd / "src/f.c").read_bytes() == replacement.read_bytes()
        (cwd / "build").mkdir()
        (cwd / "build/game.z64").write_bytes(b"ROM" if success else b"BAD")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(gate.subprocess, "run", build)
    result = gate.run(repo=repo, manifest=manifest, output=tmp_path / "receipt.json",
                      staging_parent=tmp_path)
    assert result["whole_rom_verified"] is success
    assert not result["complete_c_decompilation"]
    assert original.read_bytes() == b"INCLUDE_ASM(f);\n"
    assert Path(result["workspace"]).is_dir()
    assert Path(result["build_log"]).parent == tmp_path
    assert Path(result["built_rom_artifact"]).read_bytes() == (b"ROM" if success else b"BAD")
