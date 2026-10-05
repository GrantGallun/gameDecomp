"""The corpus manifests must stay tied to the files they describe.

Every row in `corpus/rom_inventory.json` names a dump and the hash the project publishes for it.
Every row in `corpus/compiler_survey.json` names the build file its compiler was read from. These
tests fail when either drifts from the workspace, because a manifest that disagrees with reality is
worse than no manifest: it is a confident wrong answer.
"""
import hashlib
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "corpus" / "rom_inventory.json"
SURVEY = ROOT / "corpus" / "compiler_survey.json"
ALLOWLIST = ROOT / "corpus" / "n64_sources.json"
SBK1_MAKEFILE = ROOT / "external" / "snowboardkids-decomp" / "Makefile"

REQUIRED = {"file", "sha1", "size_bytes", "decomp", "evidence", "role", "usable"}


def _inventory() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def _survey() -> dict:
    return json.loads(SURVEY.read_text(encoding="utf-8"))


def test_every_inventory_row_is_complete():
    for rom in _inventory()["roms"]:
        assert REQUIRED <= set(rom), rom.get("file")


def test_the_one_unsupported_dump_is_named_as_such():
    """Majora's Mask Europe came from a release the decomp does not build."""
    by_name = {r["file"]: r for r in _inventory()["roms"]}
    unusable = [r for r in _inventory()["roms"] if not r["usable"]]
    assert [r["file"] for r in unusable] == [
        "roms/Legend of Zelda, The - Majora's Mask (Europe) (En,Fr,De,Es) (Rev 1).z64"]
    assert "n64-us" in unusable[0]["evidence"]
    assert by_name["roms/Snowboard Kids (USA).z64"]["usable"]


@pytest.mark.parametrize("rom", _inventory()["roms"], ids=lambda r: r["file"])
def test_a_dump_on_disk_matches_the_hash_the_manifest_records(rom):
    path = ROOT / rom["file"]
    if not path.exists():
        pytest.skip("dump not present in this workspace")
    assert path.stat().st_size == rom["size_bytes"]
    assert hashlib.sha1(path.read_bytes()).hexdigest() == rom["sha1"]


def test_survey_rows_that_claim_an_allowlist_entry_really_have_one():
    allowed = {r["id"] for r in json.loads(ALLOWLIST.read_text(encoding="utf-8"))["repositories"]}
    by_id = {"Diddy Kong Racing": "dkr", "Pokemon Snap": "pokemonsnap"}
    claimed = [p for p in _survey()["projects"] if p["in_allowlist"]]
    assert claimed, "no survey row claims an allowlist entry; the join is untested"
    for project in claimed:
        assert by_id[project["name"]] in allowed, project["name"]


def test_every_survey_row_cites_evidence_and_a_tier():
    tiers = set(_survey()["priority"])
    for project in _survey()["projects"]:
        assert project["tier"] in tiers, project["name"]
        assert len(project["evidence"]) > 40, project["name"]


def test_the_benchmark_isa_in_the_survey_matches_the_benchmark_makefile():
    """The survey once ranked repositories against an inferred -mips2. Fail if that returns."""
    recipe = _survey()["benchmark"]["game_code_recipe"]
    assert recipe["isa"] == "-mips1" and recipe["opt"] == "-O2"
    if not SBK1_MAKEFILE.exists():
        pytest.skip("SBK1 checkout not present")
    makefile = SBK1_MAKEFILE.read_text(encoding="utf-8", errors="replace")
    assert re.search(r"(?m)^C_MIPS\s*=\s*-mips1\s*$", makefile)
    assert re.search(r"(?m)^C_OPT\s*=\s*-O2\s*$", makefile)
    # The benchmark really does define this for every C translation unit, game code included.
    assert "-DCOMPILING_LIBULTRA" in makefile.split("CFLAGS")[0].split("C_DEFINES")[1]
