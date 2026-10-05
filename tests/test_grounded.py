"""Every claim the mock-based tests make, re-asserted against real data.

Each test here names the bug it would have caught. See tests/conftest.py.
"""

import pytest

from solver import buildtypes, structgen, typedecl, unknowns, workspace

pytestmark = pytest.mark.grounded


# --- the mocks themselves, validated against the real queries ---------------

def test_the_fake_conn_routing_assumption_still_holds():
    """tests/test_unknowns.py routes its fake on 'func_addr' in the SQL.

    The first version routed on 'FROM evidence', which BOTH queries contain,
    so the layout query received the globals rows. A fake that drifts from the
    real queries silently tests the wrong thing, so the discriminator it relies
    on is pinned here.
    """
    assert "func_addr" in structgen.ACCESSES
    assert "FROM evidence" in structgen.ACCESSES


# --- bug 1: global base spelling --------------------------------------------

def test_global_bases_use_uppercase_hex_digits(kb):
    """`global:0x801107D8`, not `0x801107d8`. 1,033 of 1,166 bases have letters."""
    bases = [r[0] for r in kb.execute(
        "SELECT DISTINCT base FROM evidence WHERE base LIKE 'global:%'")]
    assert bases, "no global bases at all -- the query or the tier changed"
    with_letters = [b for b in bases if any(c in b.split(":")[1][2:]
                                            for c in "ABCDEFabcdef")]
    assert with_letters, "expected hex-letter addresses to exist"
    assert all(b.split(":")[1][2:].upper() == b.split(":")[1][2:]
               for b in with_letters), "spelling changed; _global_evidence must follow"


def test_global_evidence_resolves_a_letter_bearing_address(kb):
    """The exact case that returned zero citations while the unit test passed."""
    slots, loaded, stored, cites = unknowns._global_evidence(kb, 0x801107D8)
    assert cites, "no citations for gRelocatableHeapFreeBlockStack"
    assert slots and slots[0]["width"] in (1, 2, 4)


# --- bug 2: absent m2c body -------------------------------------------------

def test_absent_body_fires_on_a_real_blank_draft(kb, repo_path):
    """noopThreeArgs' base.c is a comment; 26 leaves looked 'fully known'.

    This used to read that workspace directly, which made the test a snapshot of a mutable tree: the
    blank drafts are a DEFECT being drained (`eval/m2c_redraft.py` regenerated 66 of the 79, and this
    test went red because one of them was `noopThreeArgs`). The behaviour under test is "a draft with
    no body yields exactly one `absent_body` unknown of full weight", so it is asserted on the real
    marker text rather than on whichever workspace still happens to carry it.
    """
    marker = workspace.m2c_draft(repo_path / "nonmatchings" / "noopThreeArgs")
    real = marker if "m2c failed" in marker else ""
    drafts = [real] if real.strip() else ['#include "common.h"\n\n// file is blank because m2c '
                                          "failed to decompile function\n"]
    for draft in drafts:
        if not draft.strip():
            continue
        assert typedecl.definition_params(draft, "noopThreeArgs") is None
        found = unknowns.enumerate_unknowns(kb, "noopThreeArgs", draft, set(), {})
        assert [u.kind for u in found] == ["absent_body"]
        assert unknowns.free_weight(found) == 100


# --- bug 3: heldout key spelling --------------------------------------------

def test_heldout_splits_key_on_function(kb):
    """Reading entry["name"] returned {} and disabled the guard entirely."""
    import json
    import pathlib
    from eval import zero_token_harvest as zth
    names = zth.heldout_names(pathlib.Path("eval/sets"))
    assert len(names) >= 49
    raw = json.loads(pathlib.Path("eval/sets/sbk1_v3.json").read_text())
    assert all("function" in e for e in raw["heldout"])


# --- the LEDGER.md gates, on real drafts rather than fixtures ---------------

def test_ledger_gate_one_fdrumsoff(kb, repo_path):
    draft = workspace.m2c_draft(repo_path / "nonmatchings" / "Fdrumsoff")
    if not draft.strip():
        pytest.skip("workspace not bootstrapped")
    found = unknowns.enumerate_unknowns(
        kb, "Fdrumsoff", draft, buildtypes.type_names(repo_path), {})
    types = [u for u in found if u.kind == "undeclared_type"]
    assert len(types) == 1
    assert types[0].capabilities["accessed"] == [
        {"offset": 112, "width": 4, "signed": None}]
    assert types[0].resolver == "solver.typedecl"


def test_ledger_gate_two_global_width_comes_from_evidence(kb, repo_path):
    fn = "releaseRelocatableHeapBlockMetadata"
    draft = workspace.m2c_draft(repo_path / "nonmatchings" / fn)
    if not draft.strip():
        pytest.skip("workspace not bootstrapped")
    found = unknowns.enumerate_unknowns(
        kb, fn, draft, buildtypes.type_names(repo_path),
        unknowns.symbol_table(repo_path))
    globs = {u.subject: u for u in found if u.kind == "undeclared_global"}
    used = globs["global:0x80110918"]
    assert used.capabilities["accessed"][0]["width"] == 2   # NOT symbol_addrs
    assert used.cites, "a global claim with no provenance"
    stack = globs["global:0x801107D8"]
    assert stack.capabilities["subscripted"] == [
        "gRelocatableHeapFreeBlockStack"]
    assert any(u.kind == "field_extent" and u.status == "unpinnable"
               for u in found)


# --- the contamination line, enforced rather than documented ----------------

def test_symbol_table_does_not_read_curated_sizes(repo_path):
    """symbol_addrs.txt carries `// size:0x4`. Joining on address is
    mechanical; taking the size is reading the decomp team's answer."""
    assert "size" not in unknowns.SYMBOL_LINE.pattern
    line = "gRacePlayerHitCueId = 0x80121D50; // size:0x4"
    match = unknowns.SYMBOL_LINE.search(line)
    assert match.groups() == ("gRacePlayerHitCueId", "0x80121D50")
    assert unknowns.symbol_table(repo_path).get("gRacePlayerHitCueId") == 0x80121D50


def test_ledger_does_not_depend_on_the_contaminating_module():
    """solver/project_headers.py adds reconstructed include/game headers.

    An earlier version of this test grepped module source for the string
    "include/game" and failed against the docstring SAYING it is not read --
    a text search cannot distinguish a path from a promise. The structural
    property is the real one: the ledger must not import that module.
    """
    import ast
    import inspect
    for module in (unknowns, typedecl):
        tree = ast.parse(inspect.getsource(module))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        assert not any("project_headers" in name for name in imported)
