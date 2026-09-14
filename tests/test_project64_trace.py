"""Trace-runner job validation and multi-session file organisation (no emulator)."""
import json

import pytest

from eval import project64_trace as t


def multi_job(**changes):
    job = {"schema_version": 1, "kind": "multi", "duration_seconds": 60,
           "portable_dir": "C:/p", "rom_path": "C:/rom.z64", "output_dir": "C:/out",
           "max_calls": 6, "max_events": 1000, "max_window_ms": 8000, "max_abandons": 3, "max_depth": 4,
           "functions": [{"name": "f", "entry": 0x80001000, "end": 0x80001010, "every": 1},
                         {"name": "g", "entry": 0x80002000, "end": 0x80002008, "every": 30}]}
    job.update(changes)
    return job


def test_multi_job_validation_accepts_and_rejects():
    t._validate(multi_job())
    with pytest.raises(ValueError, match="duplicate"):
        t._validate(multi_job(functions=[{"name": "f", "entry": 0x80001000, "end": 0x80001010, "every": 1}] * 2))
    with pytest.raises(ValueError, match="invalid multi function row"):
        t._validate(multi_job(functions=[{"name": "f", "entry": 0x80001001, "end": 0x80001010, "every": 1}]))
    with pytest.raises(ValueError, match="max_depth"):
        t._validate(multi_job(max_depth=0))


def test_organize_moves_calls_into_function_folders_and_checks_content(tmp_path):
    for name, index in (("f", 0), ("f", 1), ("g_2", 0)):
        (tmp_path / f"call-{name}-{index}.json").write_text(json.dumps(
            {"kind": "project64-call-trace", "function": name, "index": index}))
    moved = t.organize_multi(tmp_path)
    assert sorted(moved) == ["f", "g_2"] and len(moved["f"]) == 2
    assert (tmp_path / "g_2" / "call-0.json").is_file()
    (tmp_path / "call-h-0.json").write_text(json.dumps({"kind": "project64-call-trace", "function": "other"}))
    with pytest.raises(ValueError, match="does not match"):
        t.organize_multi(tmp_path)


def test_trace_config_keeps_emulated_audio_enabled():
    # `[Settings] Enable Audio=0` stalled the game; muting is done at the OS.
    config = t._config("project64_trace_multi.js")
    assert "Enable Audio=0" not in config
    assert "Autorun Scripts=project64_trace_multi.js" in config
