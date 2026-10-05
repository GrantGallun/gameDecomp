"""The sandbox must refuse what it exists to refuse, and the grabber must not silently decline.

Each test pins a specific way the sandbox could fail open. The fire test at the bottom runs the real
extraction and is opt-in, because it builds a C++ tool and takes minutes.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from tools import corpus_grabber as cg
from tools import sandbox_extract as sx

SELECTOR = "dkr@pal.v80"
ROM = "roms/Diddy Kong Racing (Europe) (En,Fr,De) (Rev 1).z64"
PINNED = "b7f628073237b3d211d40406aa0884ff8fdd70d5"


# --- guarantee 1: the ROM must be the pinned revision -------------------------

def test_a_rom_that_is_not_the_pinned_one_is_refused(tmp_path):
    wrong = tmp_path / "wrong.z64"
    wrong.write_bytes(b"\x80\x37\x12\x40" + b"\x00" * 64)
    with pytest.raises(sx.Refused, match="but this variant pins"):
        sx.verify_rom(wrong, PINNED)


def test_a_missing_rom_is_refused(tmp_path):
    with pytest.raises(sx.Refused, match="rom not found"):
        sx.verify_rom(tmp_path / "absent.z64", PINNED)


def test_the_pinned_rom_verifies_and_reports_its_digest(tmp_path):
    good = tmp_path / "good.z64"
    good.write_bytes(b"\x80\x37\x12\x40" + b"\x00" * 64)
    digest = sx.verify_rom(good, sx._sha1(good))
    assert digest == sx._sha1(good)


def test_the_dkr_recipe_declares_a_rom_a_destination_and_outputs():
    plan = cg.load_recipes()[SELECTOR]["extraction"]
    assert plan["rom_file"] == ROM
    assert plan["rom_destination"].startswith("baseroms/")
    assert plan["outputs"] == ["include/asset_enums.h"]
    assert plan["commands"] and all(isinstance(step, list) for step in plan["commands"])


# --- guarantee 3: no network --------------------------------------------------

def test_every_sandboxed_command_denies_the_network(tmp_path):
    argv = sx.bwrap_argv(tmp_path)
    assert "--unshare-net" in argv
    assert argv[argv.index("--bind") + 1] == str(tmp_path)      # only the throwaway dir is writable
    assert argv[argv.index("--ro-bind") + 1] == "/"             # the rest of the host is read-only


def test_each_masked_path_becomes_an_empty_tmpfs(tmp_path):
    masked = (Path("/home/x/decomp/corpus"), Path("/home/x/decomp/sbk1/src"))
    argv = sx.bwrap_argv(tmp_path, masked)
    for path in masked:
        assert argv[argv.index(str(path)) - 1] == "--tmpfs"
    assert argv.count("--tmpfs") == 1 + len(masked)             # /tmp plus the masks


def test_the_corpus_checkout_the_grabber_compiles_from_is_masked():
    """If that tree were writable inside the sandbox, 'throwaway' would be a claim, not a property."""
    candidates = sx.mask_paths(exists=lambda _path: True)
    assert cg.CORPUS_DIR in candidates
    assert set(cg.EVALUATION_SOURCES) <= set(candidates)


# --- guarantee 4: only declared outputs come back -----------------------------

def test_a_glob_reaching_outside_the_sandbox_is_refused(tmp_path):
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "asset_enums.h").write_text("/* generated */\n")
    with pytest.raises(sx.Refused, match="escapes the sandbox"):
        sx.resolve_outputs(tmp_path, ["../include/asset_enums.h"])


def test_only_files_matching_the_declared_outputs_are_collected(tmp_path):
    work = tmp_path / "work"
    (work / "include").mkdir(parents=True)
    (work / "include" / "asset_enums.h").write_text("/* generated */\n")
    (work / "include" / "undeclared.h").write_text("nope\n")
    (work / "assets.bin").write_bytes(b"\x00" * 16)
    destination = tmp_path / "out"
    copied = sx.collect(work, ["include/asset_enums.h"], destination)
    assert [row["path"] for row in copied] == ["include/asset_enums.h"]
    assert (destination / "include" / "asset_enums.h").exists()
    assert not (destination / "include" / "undeclared.h").exists()
    assert not (destination / "assets.bin").exists()
    assert copied[0]["bytes"] == (destination / "include" / "asset_enums.h").stat().st_size
    assert copied[0]["bytes"] > 0 and len(copied[0]["sha256"]) == 64


def test_a_variant_without_an_extraction_block_is_refused():
    """`dkr` is us.v77: no US dump is held, so there is nothing to extract and it says so."""
    with pytest.raises(sx.Refused, match="declares no extraction step"):
        sx.load_extraction("dkr")


# --- the grabber must not silently decline ------------------------------------

def test_the_grabber_refuses_when_a_declared_extraction_has_not_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "GENERATED_ROOT", tmp_path / "nothing-here")
    with pytest.raises(cg.Refused, match="extraction step but its outputs are not present"):
        cg.grab(SELECTOR, out_dir=tmp_path)


def test_generated_headers_become_include_roots_under_their_own_directory(monkeypatch, tmp_path):
    """`#include "asset_enums.h"` must resolve, so the PARENT of the output is the include root."""
    generated = tmp_path / "generated"
    (generated / "dkr.pal.v80" / "include").mkdir(parents=True)
    (generated / "dkr.pal.v80" / "include" / "asset_enums.h").write_text("enum {};\n")
    monkeypatch.setattr(cg, "GENERATED_ROOT", generated)
    entry = cg.load_recipes()[SELECTOR]
    assert cg.generated_include_dirs(entry) == [generated / "dkr.pal.v80" / "include"]
    assert [Path(row["path"]).name for row in cg.generated_headers(entry)] == ["asset_enums.h"]


def test_a_recipe_without_extraction_contributes_no_include_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "GENERATED_ROOT", tmp_path)
    assert cg.generated_include_dirs(cg.load_recipes()["dkr"]) == []


# --- fire test: the real extraction, opt-in -----------------------------------

@pytest.mark.skipif(os.environ.get("GAMEDECOMP_SANDBOX_FIRE") != "1",
                    reason="builds a C++ tool and runs splat; set GAMEDECOMP_SANDBOX_FIRE=1")
def test_the_real_extraction_produces_the_header_the_files_need(tmp_path):
    entry = cg.load_recipes()[SELECTOR]
    if not (cg.ROOT / entry["extraction"]["rom_file"]).exists():
        pytest.skip("ROM not present in this workspace")
    if shutil.which("bwrap") is None:
        pytest.skip("bwrap unavailable")
    receipt = sx.extract(SELECTOR, generated_root=tmp_path)
    produced = {row["path"] for row in receipt["outputs"]}
    assert "include/asset_enums.h" in produced
    assert receipt["rom"]["sha1"] == entry["rom"]["pinned_sha1"]
    assert receipt["guarantees"]["network_denied"] is True
    assert all(step["exit"] == 0 for step in receipt["steps"])
    assert receipt["outputs"][0]["bytes"] > 1000
    assert list(tmp_path.rglob("asset_enums.h")), "the header must land in the generated root"
