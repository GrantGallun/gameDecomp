"""The grabber's guards must refuse what they exist to refuse, and the pipeline must fire on DKR."""
import json
from pathlib import Path

import pytest

from tools import corpus_grabber as cg


# --- repository admission ----------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://github.com/cdlewis/snowboardkids-decomp.git",
    "https://github.com/cdlewis/snowboardkids2-decomp.git",
    "https://github.com/SomeFork/SnowboardKids2-Decomp",
    "https://github.com/cdlewis/snowboardkids2-recomp",       # a recomp carries the decomp's symbols and types
    "https://github.com/someone/Snowboard-Kids-PC-Port.git",
])
def test_evaluation_repositories_are_refused(url):
    with pytest.raises(cg.Refused, match="evaluation repository"):
        cg.admissible(url)


@pytest.mark.parametrize("url", [
    "http://github.com/davidsm64/diddy-kong-racing.git",
    "https://gitlab.com/someone/decomp.git",
    "https://github.com.evil.example/x.git",
])
def test_only_https_github_is_admitted(url):
    with pytest.raises(cg.Refused, match="host not allowlisted"):
        cg.admissible(url)


def test_real_recipes_join_the_allowlist():
    recipes = cg.load_recipes()
    assert "dkr" in recipes
    assert recipes["dkr"]["url"].startswith("https://github.com/")
    assert len(recipes["dkr"]["commit"]) == 40


def test_a_recipe_without_an_allowlist_entry_is_refused(tmp_path):
    allow = json.loads(cg.ALLOWLIST.read_text())
    recipes = {"schema_version": 1, "repositories": [{"id": "not-allowlisted", "flags": []}]}
    (tmp_path / "allow.json").write_text(json.dumps(allow))
    (tmp_path / "recipes.json").write_text(json.dumps(recipes))
    with pytest.raises(cg.Refused, match="no allowlist entry"):
        cg.load_recipes(tmp_path / "allow.json", tmp_path / "recipes.json")


# --- build variants -----------------------------------------------------------

def test_variants_of_one_repository_are_separate_selectors_sharing_a_checkout():
    """DKR ships five ROM revisions from one source tree; only the VERSION define differs."""
    recipes = cg.load_recipes()
    assert "dkr" in recipes and "dkr@pal.v80" in recipes
    default, variant = recipes["dkr"], recipes["dkr@pal.v80"]
    assert default["url"] == variant["url"] and default["commit"] == variant["commit"]
    assert default["id"] == variant["id"] == "dkr"        # one checkout, keyed by allowlist id
    differing = [a for a, b in zip(default["flags"], variant["flags"]) if a != b]
    assert differing == ["-DVERSION_us_v77"]
    assert "-DVERSION_pal_v80" in variant["flags"]


def test_pal_v80_pins_the_dump_this_workspace_holds():
    """A recipe is only useful if it names the revision of the ROM actually on disk."""
    import hashlib
    rom = Path("roms/Diddy Kong Racing (Europe) (En,Fr,De) (Rev 1).z64")
    if not rom.exists():
        pytest.skip("DKR ROM not present in this workspace")
    digest = hashlib.sha1(rom.read_bytes()).hexdigest()
    assert digest == cg.load_recipes()["dkr@pal.v80"]["rom"]["pinned_sha1"]


def test_two_recipes_answering_to_one_selector_are_refused(tmp_path):
    allow = json.loads(cg.ALLOWLIST.read_text())
    recipes = {"schema_version": 1, "default_variant": "a", "repositories": [
        {"id": "dkr", "variants": {"a": {"flags": []}, "b": {"flags": []}}},
        {"id": "dkr", "variants": {"a": {"flags": []}}},
    ]}
    (tmp_path / "allow.json").write_text(json.dumps(allow))
    (tmp_path / "recipes.json").write_text(json.dumps(recipes))
    with pytest.raises(cg.Refused, match="both answer to dkr"):
        cg.load_recipes(tmp_path / "allow.json", tmp_path / "recipes.json")


# --- per-file flags -----------------------------------------------------------

def test_dkr_declares_its_one_per_file_override():
    """`get_stack_pointer.c` returns `__$sp`, which cfe rejects without -dollar."""
    entry = cg.load_recipes()["dkr@pal.v80"]
    rules = entry["per_file_flags"]
    assert [rule["path"] for rule in rules] == ["src/get_stack_pointer.c"]
    assert rules[0]["flags"] == ["-dollar"]
    assert "Makefile:293" in rules[0]["why"]


def test_per_file_flags_apply_to_that_path_and_no_other():
    entry = cg.load_recipes()["dkr@pal.v80"]
    assert cg.per_file_flags(entry, "src/get_stack_pointer.c") == ["-dollar"]
    assert cg.per_file_flags(entry, "src/objects.c") == []
    assert cg.per_file_flags(entry, "src/racer.c") == []


def test_a_recipe_without_per_file_rules_adds_no_flags():
    assert cg.per_file_flags({"id": "x"}, "src/anything.c") == []


# --- evaluation contamination guard ------------------------------------------

LONG_BODY = """{
    s32 i;
    s32 total;

    total = 0;
    for (i = 0; i < count; i++) {
        if (table[i].flags & 0x10) {
            total += table[i].value * 3;
        } else {
            total -= table[i].value >> 2;
        }
    }
    return total;
}"""


def test_exact_duplicate_of_an_evaluation_function_is_rejected_despite_renaming():
    index = cg.EvaluationIndex()
    index.add(LONG_BODY)
    renamed = LONG_BODY.replace("total", "acc").replace("table", "entries").replace("count", "n")
    assert index.verdict(renamed) == "exact-duplicate"


def test_near_duplicate_is_rejected():
    index = cg.EvaluationIndex()
    index.add(LONG_BODY)
    edited = LONG_BODY.replace("return total;", "total += 1;\n    return total;")
    assert index.verdict(edited) == "near-duplicate"


def test_unrelated_function_passes():
    index = cg.EvaluationIndex()
    index.add(LONG_BODY)
    other = "{\n    osViBlack(1);\n    gVideoDeltaCounter = 0;\n    D_801262E4 = 3;\n}"
    assert index.verdict(other) == "clean"


def test_a_guard_that_cannot_see_its_evaluation_source_refuses(tmp_path):
    with pytest.raises(cg.Refused, match="evaluation source missing"):
        cg.EvaluationIndex.build((tmp_path / "does-not-exist",))


def test_an_empty_evaluation_index_refuses(tmp_path):
    (tmp_path / "empty.c").write_text("/* no functions */\n")
    with pytest.raises(cg.Refused, match="index is empty"):
        cg.EvaluationIndex.build((tmp_path,))


# --- preprocessor conditionals ------------------------------------------------

# verbatim shape of DKR src/asset_loading.c at 84f0ea5
DMACOPY = """\
void dmacopy(u32 romOffset, u32 ramAddress, s32 numBytes) {
#if VERSION >= VERSION_79
    OSMesg msg = NULL;
    osRecvMesg(&gDmaMutex, &msg, OS_MESG_BLOCK);
    dmacopy_internal(romOffset, ramAddress, numBytes);
    osSendMesg(&gDmaMutex, (OSMesg) 1, OS_MESG_NOBLOCK);
}

// Looks like v2 ROMs made an alternate version of this function, and this is the original.
void dmacopy_internal(u32 romOffset, u32 ramAddress, s32 numBytes) {
#endif
    s32 bytesLeft;
    bytesLeft = numBytes;
}
"""


def test_a_conditional_spanning_a_function_boundary_excludes_both_functions():
    """At v77 the compiled `dmacopy` is dmacopy_internal's body; neither text is a faithful pair."""
    from tools import n64_corpus
    functions = list(n64_corpus.extract_functions(DMACOPY))
    assert [f["name"] for f in functions] == ["dmacopy", "dmacopy_internal"]
    assert not any(cg.conditional_free(DMACOPY, str(f["definition"])) for f in functions)


def test_a_function_after_a_closed_conditional_is_kept():
    source = "#if 0\nstatic int unused;\n#endif\n\ns32 plain(s32 a) {\n    return a + 1;\n}\n"
    from tools import n64_corpus
    (fn,) = n64_corpus.extract_functions(source)
    assert cg.conditional_free(source, str(fn["definition"]))


def test_a_function_inside_an_open_conditional_is_excluded():
    source = "#ifdef NON_MATCHING\ns32 attempt(s32 a) {\n    return a;\n}\n#else\nINCLUDE_ASM(x);\n#endif\n"
    from tools import n64_corpus
    (fn,) = n64_corpus.extract_functions(source)
    assert not cg.conditional_free(source, str(fn["definition"]))


# --- fire test on the real repository ----------------------------------------

DKR = cg.CORPUS_DIR / "dkr"
ready = (DKR / ".git").is_dir() and cg.TOOLCHAINS["ido-5.3"].exists() and all(
    Path(p).is_dir() for p in cg.EVALUATION_SOURCES)


@pytest.mark.skipif(not ready, reason="DKR clone, IDO 5.3 or evaluation sources unavailable")
def test_grab_dkr_produces_pairs_and_explains_every_function(tmp_path):
    receipt = cg.grab("dkr", out_dir=tmp_path)
    assert receipt["pairs"] >= 1
    accounted = (receipt["pairs"] + receipt.get("functions_conditional", 0)
                 + sum(receipt["guard_rejected"].values()) + receipt["functions_without_code"])
    assert accounted == receipt["functions_seen"]
    assert receipt["functions_without_code"] == 0
    row = json.loads((tmp_path / "dkr.jsonl").read_text().splitlines()[0])
    assert row["verification"].startswith("upstream-matched")
    assert row["asm"] and row["source"].count("{") == row["source"].count("}")
