"""Generated pairs must be deterministic, contamination-free, and FIRE on what each family targets.

The fire tests compile with the game's real IDO recipe and skip where that toolchain is absent.
"""
import shutil
from pathlib import Path

import pytest

from solver import compilefix
from tools import synthetic_corpus as sc


def test_generation_is_deterministic_in_family_and_seed():
    for family in sc.FAMILIES:
        assert sc.generate(family, 7) == sc.generate(family, 7)
        assert sc.generate(family, 7)[1] != sc.generate(family, 8)[1], family


def test_every_generated_name_is_synthetic():
    """A generated pair must never be mistakable for a game symbol."""
    for family in sc.FAMILIES:
        for seed in range(20):
            name, source = sc.generate(family, seed)
            assert name.startswith(sc.NAME_PREFIX)
            assert f"{name}(" in source


def test_generated_sources_are_brace_balanced_and_use_the_sanctioned_loop_dialect():
    """The build bans the `do` token; the corpus should not teach it."""
    for family in sc.FAMILIES:
        for seed in range(50):
            _name, source = sc.generate(family, seed)
            assert not compilefix.brace_imbalance(source), (family, seed)
            assert not __import__("re").search(r"\bdo\b", source), (family, seed)


SELF_CANCELLING = __import__("re").compile(r"\((\w+) [-&^|] \1\)|\b(\w+) = \2;")


def test_no_generated_expression_cancels_itself():
    """`(arg1 - arg1)` folds away; its source term would have no assembly counterpart."""
    for family in sc.FAMILIES:
        for seed in range(300):
            _name, source = sc.generate(family, seed)
            assert not SELF_CANCELLING.search(source), (family, seed)


def test_if_chain_assignments_stay_live():
    """15 of the first 150 if_chain samples lost a branch to a dead assignment."""
    import re
    for seed in range(300):
        _name, source = sc.generate("if_chain", seed)
        params = re.findall(r"s32 (arg\d)", source.split("{")[0])
        final = [line for line in source.splitlines() if line.strip().startswith("return")][-1]
        assert all(p in final for p in params), (seed, final)


# verbatim `mips-linux-gnu-objdump -dr --no-show-raw-insn` of an IDO -O2 -mips1 dense switch
SWITCH_DUMP = """\
00000000 <syn_switch>:
   0:	sltiu	at,a0,8
   4:	beqz	at,68 <syn_switch+0x68>
   8:	li	v0,-1
   c:	sll	t6,a0,0x2
  10:	lui	at,0x0
			10: R_MIPS_HI16	.rodata
  14:	addu	at,at,t6
  18:	lw	t6,0(at)
			18: R_MIPS_LO16	.rodata
  1c:	nop
  20:	jr	t6
  24:	nop
  28:	jr	ra
  2c:	li	v0,11
  68:	jr	ra
"""


def test_features_recognise_a_real_ido_jump_table():
    listing = sc.function_listing(SWITCH_DUMP, "syn_switch")
    feat = sc.features(listing)
    assert feat["jump_table"] is True
    assert feat["branches"] == 1
    assert feat["backward_branches"] == 0
    assert feat["instructions"] == 13


# verbatim IDO output for one call crossed: values spilled to stack slots, no s-register
SINGLE_CALL = [
    "   0:\taddiu\tsp,sp,-24", "   4:\tsw\tra,20(sp)", "   8:\tsw\ta0,24(sp)",
    "   c:\tsll\tt6,a0,0x5", "  10:\tsw\ta1,28(sp)", "  14:\tsw\ta2,32(sp)",
    "  18:\tjal\t0 <syn_saved_regs_0>", "\t\t\t18: R_MIPS_26\tsyn_ext",
    "  1c:\taddiu\ta0,t6,-227", "  20:\tlw\tt7,24(sp)", "  24:\tlw\tra,20(sp)",
    "  34:\tjr\tra", "  38:\taddiu\tsp,sp,24",
]


def test_features_separate_stack_spills_from_saved_registers():
    """The probe that retired the one-call s-register assumption."""
    feat = sc.features(SINGLE_CALL)
    assert feat["saved_registers"] == ["ra"]
    assert feat["stack_spills"] == 3
    assert feat["calls"] == 1
    assert sc.FAMILIES["stack_spill"].expect(feat)
    assert not sc.FAMILIES["saved_regs"].expect(feat)


def test_features_do_not_call_a_plain_return_a_jump_table():
    listing = ["   0:\tjr\tra", "   4:\tli\tv0,1"]
    assert sc.features(listing)["jump_table"] is False


def test_function_listing_stops_at_the_next_symbol():
    dump = SWITCH_DUMP + "\n00000070 <other>:\n  70:\tjr\tra\n"
    assert all("70:" not in line for line in sc.function_listing(dump, "syn_switch"))


# --- fire tests against the real compiler ------------------------------------

TOOLCHAIN = (sc.REPO / "tools/ido-recomp/linux/cc").exists() and shutil.which(sc.OBJDUMP)
needs_toolchain = pytest.mark.skipif(not TOOLCHAIN, reason="IDO recipe or objdump unavailable")


@pytest.fixture(scope="module")
def resolved():
    return sc.recipe(sc.REPO)


@needs_toolchain
@pytest.mark.parametrize("family", sorted(sc.FAMILIES))
def test_each_family_compiles_and_fires_on_its_first_seeds(family, resolved, tmp_path):
    """Declining is the easy half. Each family must produce what it targets."""
    rows = [sc.compile_one(sc.REPO, resolved, family, seed, tmp_path) for seed in range(5)]
    assert all(r["compiled"] for r in rows), [r["stderr"][:200] for r in rows if not r["compiled"]]
    assert sum(r["expected"] for r in rows) >= 4, [(r["seed"], r["features"]) for r in rows]


@needs_toolchain
def test_dense_and_sparse_switches_are_distinguished_by_the_compiler(resolved, tmp_path):
    dense = sc.compile_one(sc.REPO, resolved, "switch_dense", 0, tmp_path)
    sparse = sc.compile_one(sc.REPO, resolved, "switch_sparse", 0, tmp_path)
    assert dense["features"]["jump_table"] and not sparse["features"]["jump_table"]


@needs_toolchain
def test_same_source_compiles_to_identical_bytes(resolved, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    a = sc.compile_one(sc.REPO, resolved, "saved_regs", 3, first)
    b = sc.compile_one(sc.REPO, resolved, "saved_regs", 3, second)
    assert a["compiled"] and a["asm_sha256"] == b["asm_sha256"]
