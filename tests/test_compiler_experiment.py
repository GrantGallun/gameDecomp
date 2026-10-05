"""Bounded, candidate-only direct compiler phase experiments."""

import hashlib
import json
from pathlib import Path
import sys

from solver import compiler_experiment


SOURCE = "int f(void) { return 7; }\n"


def _setup(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    ws = repo / "nonmatchings" / "f"
    ws.mkdir(parents=True)
    (ws / ".compiler-target.json").write_text(json.dumps(
        {"function": "f", "target": "build/src/menu/f.o"}))
    tools = repo / "tools"
    tools.mkdir()
    (tools / "textconv.py").write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[3]).write_bytes(pathlib.Path(sys.argv[2]).read_bytes())\n")
    (tools / "charmap.txt").write_text("unused")
    compiler = tools / "cc"
    compiler.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "src = pathlib.Path(args[-1])\n"
        "if '-S' in args:\n"
        "    pathlib.Path(src.stem + '.s').write_text('f:\\n\\tli $2, 7\\n')\n"
        "elif '-c' in args:\n"
        "    pathlib.Path(args[args.index('-o')+1]).write_bytes(b'OBJECT7')\n"
        "else:\n"
        "    sys.exit(3)\n")
    compiler.chmod(0o755)
    monkeypatch.setattr(compiler_experiment.compiler_recipe, "resolve", lambda _repo, target: {
        "target": target, "command": [sys.executable, str(compiler), "-c", "-O2", "-Iinclude", "-Isrc/menu"],
        "makefile_sha256": "make-hash", "projection_sha256": "projection-hash"})
    # The production source projection is separately owned; keep this fixture focused
    # on phase execution and correspondence.
    monkeypatch.setattr(compiler_experiment, "_compile_source", lambda _repo, source: source)
    ordinary = ws / "f.o"
    ordinary.write_bytes(b"OBJECT7")
    return repo, ws, ordinary


def test_direct_object_reproduction_gates_pre_as1_output(tmp_path, monkeypatch):
    repo, ws, ordinary = _setup(tmp_path, monkeypatch)
    report = compiler_experiment.inspect(repo, ws, "f", SOURCE, tmp_path / "phase",
                                         hypothesis="constant load placement", object_path=ordinary)
    assert report["status"] == "captured"
    assert report["comparable"] is True
    assert report["compile_units"] == 2
    assert report["source_sha256"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert report["recipe"]["target"] == "build/src/menu/f.o"
    assert report["recipe"]["makefile_sha256"] == "make-hash"
    assert report["reproduction"]["status"] == "raw_object_equal"
    assert report["assembly"]["text"].replace("\r\n", "\n") == "f:\n\tli $2, 7\n"
    assert Path(report["assembly"]["path"]).read_bytes().decode() == report["assembly"]["text"]
    assert all("-I" in " ".join(call["command"]) for call in report["invocations"])


def test_different_ordinary_object_keeps_capture_diagnostic_only(tmp_path, monkeypatch):
    repo, ws, ordinary = _setup(tmp_path, monkeypatch)
    ordinary.write_bytes(b"OTHER")
    report = compiler_experiment.inspect(repo, ws, "f", SOURCE, tmp_path / "phase",
                                         hypothesis="test", object_path=ordinary)
    assert report["status"] == "captured"
    assert report["comparable"] is False
    assert report["reproduction"]["status"] == "different"
    assert report["assembly"]["text"].startswith("f:")


def test_missing_ordinary_object_never_claims_correspondence(tmp_path, monkeypatch):
    repo, ws, _ = _setup(tmp_path, monkeypatch)
    report = compiler_experiment.inspect(repo, ws, "f", SOURCE, tmp_path / "phase",
                                         hypothesis="test")
    assert report["status"] == "captured"
    assert report["comparable"] is False
    assert report["reproduction"]["status"] == "unavailable"
    assert report["compile_units"] == 2


def test_asm_macros_and_wrong_identity_decline_before_compilation(tmp_path, monkeypatch):
    repo, ws, ordinary = _setup(tmp_path, monkeypatch)
    report = compiler_experiment.inspect(repo, ws, "f", "GLOBAL_ASM(\"answer.s\")",
                                         tmp_path / "phase", hypothesis="test", object_path=ordinary)
    assert report["status"] == "unavailable"
    assert report["compile_units"] == 0
    assert not (tmp_path / "phase").exists()
    report = compiler_experiment.inspect(repo, ws, "other", SOURCE, tmp_path / "phase",
                                         hypothesis="test", object_path=ordinary)
    assert report["status"] == "unavailable"
    assert report["compile_units"] == 0


def test_reference_c_include_declines_before_compilation(tmp_path, monkeypatch):
    repo, ws, ordinary = _setup(tmp_path, monkeypatch)
    report = compiler_experiment.inspect(repo, ws, "f", '#include "../../src/f.c"\n' + SOURCE,
                                         tmp_path / "phase", hypothesis="test", object_path=ordinary)
    assert report["status"] == "unavailable"
    assert report["compile_units"] == 0
    assert not (tmp_path / "phase").exists()


def test_output_is_bounded_and_failure_does_not_claim_phase(tmp_path, monkeypatch):
    repo, ws, ordinary = _setup(tmp_path, monkeypatch)
    compiler = repo / "tools" / "cc"
    compiler.write_text("#!/usr/bin/env python3\nimport sys\nprint('X'*100000)\nsys.exit(2)\n")
    report = compiler_experiment.inspect(repo, ws, "f", SOURCE, tmp_path / "phase",
                                         hypothesis="test", object_path=ordinary)
    assert report["status"] == "error"
    assert report["compile_units"] == 1
    assert report["comparable"] is False
    assert len(json.dumps(report)) < 20000


def test_wrapper_recipe_uses_underlying_compiler_and_preserves_flags(tmp_path):
    repo, ws = tmp_path / "repo", tmp_path / "ws"
    command = ["python3", "tools/asm-processor/build.py", "tools/ido-recomp/linux/cc",
               "--", "mips-linux-gnu-as", "-G", "0", "--", "-c", "-mips1",
               "-Iinclude", "-O2"]
    direct = compiler_experiment._direct_command(command, repo, ws)
    assert direct[0] == str(repo / "tools/ido-recomp/linux/cc")
    assert "asm-processor" not in " ".join(direct)
    assert "-mips1" in direct and "-O2" in direct and "-c" in direct
    assert "-I" + str((repo / "include").resolve()) in direct


def test_object_certificate_correspondence_is_diagnostic_not_target_exactness(tmp_path, monkeypatch):
    repo, ws, ordinary = _setup(tmp_path, monkeypatch)
    ordinary.write_bytes(b"SAME-ALLOCATED-SECTIONS-WITH-DIFFERENT-METADATA")
    monkeypatch.setattr(compiler_experiment.byte_certificate, "certify",
                        lambda *args, **kwargs: {"exact": True, "status": "object_sections_exact"})
    report = compiler_experiment.inspect(repo, ws, "f", SOURCE, tmp_path / "phase",
                                         hypothesis="test", object_path=ordinary)
    assert report["comparable"] is True
    assert report["reproduction"]["status"] == "object_sections_equal"
    assert "exact" not in report
