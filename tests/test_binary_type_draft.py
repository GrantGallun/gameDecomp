from pathlib import Path
import subprocess


def test_missing_binary_declines_without_reading_existing_reference_draft(tmp_path):
    from solver import binary_type_draft as bd
    ws = tmp_path / "nonmatchings" / "f"
    ws.mkdir(parents=True)
    (ws / "base.c").write_text("void f(void) { reference_answer(); }")
    (ws / "target.s").write_text("glabel f\n")
    variants, reports = bd.variants(tmp_path, "f", ws)
    assert variants == []
    assert reports[0]["status"] == "declined"
    assert reports[0]["reference_source_used"] is False
    assert "ELF" in reports[0]["reason"]


def test_generated_draft_uses_own_prototype_for_m2c_but_not_compile(tmp_path, monkeypatch):
    from solver import binary_type_context as bc, binary_type_draft as bd
    ws = tmp_path / "nonmatchings" / "f"
    ws.mkdir(parents=True)
    (ws / "target.s").write_text("glabel f\n")
    elf = tmp_path / "build" / "binary.elf"
    elf.parent.mkdir()
    elf.write_bytes(b"binary observation source")
    model = bc.ContextModel({"rows": [{"function": "f", "addr": 4096,
        "accesses": [], "calls": [], "returns": [], "unify": [], "arity_reads": []}],
        "symbols": {}, "stack_args": {}}, {"elf_sha256": "abc"})
    monkeypatch.setattr(bc, "find_elf", lambda repo: tmp_path / "binary.elf")
    monkeypatch.setattr(bc, "load", lambda elf: model)
    wrappers = []
    def preprocess(repo, wrapper, scratch):
        wrappers.append(wrapper)
        return wrapper, {"public_headers": []}
    monkeypatch.setattr(bd, "_preprocess", preprocess)
    monkeypatch.setattr(bd, "_m2c", lambda *a, **k: subprocess.CompletedProcess([], 0, "s32 f(void) { return 7; }\n", ""))
    variants, reports = bd.variants(tmp_path, "f", ws)
    assert len(variants) == 1
    label, source = variants[0]
    assert label.startswith("binary-types:")
    assert "s32 f(void);" in wrappers[0]
    assert "s32 f(void);" not in source
    assert "s32 f(void) { return 7; }" in source
    assert '#include "common.h"' not in source
    assert reports[0]["evidence"]["elf_sha256"] == "abc"
    assert reports[0]["source_sha256"]
    assert reports[0]["input_hashes"]["elf:binary.elf"]
    assert reports[0]["input_revision"] == bd.input_revision(tmp_path)


def test_failed_preprocessor_is_a_visible_decline_not_a_reference_fallback(tmp_path, monkeypatch):
    from solver import binary_type_context as bc, binary_type_draft as bd
    ws = tmp_path / "nonmatchings" / "f"
    ws.mkdir(parents=True)
    (ws / "target.s").write_text("glabel f\n")
    (ws / "base.c").write_text("s32 f(void) { return 99; }")
    class Context:
        def context(self, *args):
            return {"declarations": "s32 f(void);\n", "own_prototype": "s32 f(void);", "evidence": {}}
    monkeypatch.setattr(bc, "find_elf", lambda repo: Path("unused"))
    monkeypatch.setattr(bc, "load", lambda elf: Context())
    def fail(*args):
        raise bd.Unavailable("public preprocessing unavailable")
    monkeypatch.setattr(bd, "_preprocess", fail)
    variants, reports = bd.variants(tmp_path, "f", ws)
    assert variants == []
    assert any("public preprocessing unavailable" in r.get("reason", "") for r in reports)
    assert all(not r["reference_source_used"] for r in reports)


def test_public_header_check_rejects_game_header_even_through_transitive_include(tmp_path):
    import pytest
    from solver import binary_type_draft as bd
    include = tmp_path / "include" / "game"
    include.mkdir(parents=True)
    header = include / "player.h"
    header.write_text("struct Answer { int realField; };")
    with pytest.raises(bd.Unavailable, match="public"):
        bd._public_dependencies(tmp_path, [header])


def test_public_header_check_accepts_isolated_repo_include_symlink(tmp_path):
    import pytest
    from solver import binary_type_draft as bd
    original = tmp_path / "original" / "include"
    original.mkdir(parents=True)
    (original / "include_asm.h").write_text("/* public assembly helper */")
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    try:
        (isolated / "include").symlink_to(original, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    result = bd._public_dependencies(isolated, [isolated / "include" / "include_asm.h"])
    assert result[0]["path"] == "include/include_asm.h"


def test_input_revision_tracks_binary_public_header_and_m2c_bytes(tmp_path):
    from solver import binary_type_draft as bd
    assert bd.input_revision(tmp_path) == bd.code_digest()
    elf = tmp_path / "build" / "game.elf"
    elf.parent.mkdir()
    elf.write_bytes(b"first binary")
    binary_revision = bd.input_revision(tmp_path)
    assert binary_revision != bd.code_digest()
    elf.write_bytes(b"changed binary")
    changed_binary = bd.input_revision(tmp_path)
    assert changed_binary != binary_revision
    header = tmp_path / "include" / "PR" / "mbi.h"
    header.parent.mkdir(parents=True)
    header.write_bytes(b"public header")
    with_header = bd.input_revision(tmp_path)
    assert with_header != changed_binary
    header.write_bytes(b"changed public header")
    assert bd.input_revision(tmp_path) != with_header
    executable = tmp_path / ".venv" / "bin" / "m2c"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"m2c launcher")
    with_m2c = bd.input_revision(tmp_path)
    executable.write_bytes(b"changed m2c launcher")
    assert bd.input_revision(tmp_path) != with_m2c
    implementation = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "m2c" / "core.py"
    implementation.parent.mkdir(parents=True)
    implementation.write_bytes(b"pass\n")
    with_code = bd.input_revision(tmp_path)
    implementation.write_bytes(b"different\n")
    assert bd.input_revision(tmp_path) != with_code


def test_input_revision_uses_pins_and_distinguishes_missing_pin_and_file(tmp_path, monkeypatch):
    from solver import binary_type_draft as bd
    elf = tmp_path / "build" / "game.elf"
    elf.parent.mkdir()
    elf.write_bytes(b"binary")
    header = tmp_path / "include" / "include_asm.h"
    header.parent.mkdir()
    header.write_bytes(b"public")
    pinned = {str(elf.resolve()): "a" * 64}
    original_read = Path.read_bytes

    def read_bytes(path):
        if path.resolve().is_relative_to(tmp_path.resolve()):
            raise AssertionError("pinned input was read from disk")
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    pinned_revision = bd.input_revision(tmp_path, pinned)
    missing_pin = bd.input_hashes(tmp_path, {})
    assert missing_pin["elf:game.elf"] == "missing-pin"
    assert pinned_revision != bd.input_revision(tmp_path, {})
    assert pinned_revision != bd.input_revision(tmp_path, {str(elf.resolve()): "b" * 64})
    elf.unlink()
    missing_file = bd.input_hashes(tmp_path, pinned)
    assert missing_file["elf:<missing>"] == "missing-file"
    assert bd.input_revision(tmp_path, pinned) != pinned_revision


def test_input_paths_stable_across_isolated_symlink_roots(tmp_path):
    import pytest
    from solver import binary_type_draft as bd
    original = tmp_path / "original"
    elf = original / "build" / "game.elf"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"binary")
    header = original / "include" / "PR" / "mbi.h"
    header.parent.mkdir(parents=True)
    header.write_bytes(b"header")
    m2c = original / ".venv" / "bin" / "m2c"
    m2c.parent.mkdir(parents=True)
    m2c.write_bytes(b"launcher")
    impl = original / ".venv" / "lib" / "python3.12" / "site-packages" / "m2c_pycparser" / "parse.py"
    impl.parent.mkdir(parents=True)
    impl.write_bytes(b"code")
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    try:
        for name in ("build", "include", ".venv"):
            (isolated / name).symlink_to(original / name, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    original_paths = bd.input_paths(original)
    isolated_paths = bd.input_paths(isolated)
    assert original_paths.keys() == isolated_paths.keys()
    assert {name: path.resolve() for name, path in original_paths.items()} == {
        name: path.resolve() for name, path in isolated_paths.items()}
    assert bd.input_revision(original) == bd.input_revision(isolated)
    pins = {str(path.resolve()): "pinned-" + name for name, path in original_paths.items() if path.is_file()}
    assert bd.input_revision(original, pins) == bd.input_revision(isolated, pins)
