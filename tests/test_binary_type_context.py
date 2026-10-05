"""Production binary contexts must bind declarations to their own evidence."""
import json


def observed(offset=0x24):
    return {"rows": [{"function": "readValue", "addr": 0x80001000,
        "accesses": [[["P", "readValue", 0], offset, 2, 1, True, "int"]],
        "calls": [], "returns": [], "unify": [], "arity_reads": [0]}],
        "symbols": {"gCount": [0x80002000, 4]}, "stack_args": {}}


def test_context_emits_observed_field_and_prototype_without_reference_inputs():
    from solver import binary_type_context as bc
    model = bc.ContextModel(observed(), {"elf_sha256": "binary-hash"})
    result = model.context("readValue", "glabel readValue\n")
    assert "u8 pad0[0x24]; s16 unk24;" in result["declarations"]
    assert "readValue(struct T0 *arg0);" in result["own_prototype"]
    assert result["evidence"]["elf_sha256"] == "binary-hash"
    assert result["evidence"]["function_address"] == 0x80001000
    assert result["evidence"]["policy"] == "D32"


def test_unknown_function_declines_instead_of_synthesizing_a_signature():
    import pytest
    from solver import binary_type_context as bc
    with pytest.raises(bc.Unavailable, match="function.*missing"):
        bc.ContextModel(observed(), {}).context("missing", "glabel missing")


def test_cache_rebuilds_for_changed_elf_and_never_reads_reference_context(tmp_path, monkeypatch):
    from solver import binary_type_context as bc
    elf = tmp_path / "game.elf"
    elf.write_bytes(b"first binary")
    forbidden = tmp_path / "score.json"
    forbidden.write_text("reference labels must not be read")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "readValue.c").write_text("this is not C")
    monkeypatch.setattr(bc, "_extract", lambda path: observed(0x24 if path.read_bytes() == b"first binary" else 0x28))
    first = bc.load(elf, tmp_path / "cache").context("readValue", "glabel readValue")
    second = bc.load(elf, tmp_path / "cache").context("readValue", "glabel readValue")
    assert second["declarations"] == first["declarations"]
    assert second["evidence"]["cache_status"] == "hit"
    elf.write_bytes(b"second binary")
    changed = bc.load(elf, tmp_path / "cache").context("readValue", "glabel readValue")
    assert "s16 unk28;" in changed["declarations"]
    assert changed["evidence"]["elf_sha256"] != first["evidence"]["elf_sha256"]


def test_corrupt_cached_payload_is_regenerated(tmp_path, monkeypatch):
    from solver import binary_type_context as bc
    elf = tmp_path / "game.elf"
    elf.write_bytes(b"binary")
    monkeypatch.setattr(bc, "_extract", lambda path: observed())
    bc.load(elf, tmp_path / "cache")
    [cache] = list((tmp_path / "cache").glob("*.json"))
    payload = json.loads(cache.read_text())
    payload["bundle"]["rows"][0]["accesses"][0][1] = 0x70
    cache.write_text(json.dumps(payload))
    result = bc.load(elf, tmp_path / "cache").context("readValue", "glabel readValue")
    assert "s16 unk24;" in result["declarations"]
    assert "unk70" not in result["declarations"]
    assert result["evidence"]["cache_status"] == "rebuilt"


def test_cache_rebuilds_when_binary_extractor_changes(tmp_path, monkeypatch):
    from pathlib import Path
    from solver import binary_type_context as bc
    elf = tmp_path / "game.elf"
    elf.write_bytes(b"binary")
    monkeypatch.setattr(bc, "_extract", lambda path: observed())
    first = bc.load(elf, tmp_path / "cache")
    original = Path.read_bytes
    def changed(path):
        data = original(path)
        return data + b"\n# revised extractor" if path.name == "evidence.py" and path.parent.name == "miner" else data
    monkeypatch.setattr(Path, "read_bytes", changed)
    second = bc.load(elf, tmp_path / "cache")
    assert second.evidence["algorithm_sha256"] != first.evidence["algorithm_sha256"]
    assert second.evidence["cache_status"] == "miss"


def test_repeated_contexts_do_not_share_function_local_type_names():
    from solver import binary_type_context as bc
    model = bc.ContextModel(observed(), {})
    a = model.context("readValue", "glabel readValue\n lui t0,%hi(gCount)")
    b = model.context("readValue", "glabel readValue")
    assert "gCount" in a["declarations"]
    assert "gCount" not in b["declarations"]
    assert "readValue(struct T0 *arg0)" in b["declarations"]
