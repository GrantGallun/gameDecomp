"""Diff-driven owners from solver/rewrites.py must be reachable by the normal mutation search."""
from pathlib import Path

import pytest

from solver import owner_rewrites, regalloc_mutations, rewrites

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = (FIXTURES / "owner_layout.c").read_text()
DIFF = (FIXTURES / "owner_layout.diff").read_text()
FUNCTION = "dispatchRacePlayerMode30Attack"


def proposals(source=SOURCE, diff=DIFF, family="owner:layout", function=FUNCTION):
    return [code for _, kind, code in regalloc_mutations.variants(source, function, diff) if kind == family]


def test_layout_owner_fires_on_the_motivating_residual():
    # Every other family proposed nothing here: the population run exhausted this root at 99.999.
    others = [kind for _, kind, _ in regalloc_mutations.variants(SOURCE, FUNCTION, DIFF)
              if not kind.startswith("owner:")]
    assert others == []
    rows = proposals()
    assert len(rows) == 1
    # The diff says the fields belong at 0x280 and 0x302; the draft declared them at 0 and 4.
    assert "-lw    v0,0x280(a0)" in DIFF and "-lh    t6,0x302(a0)" in DIFF
    assert "0x280" in rows[0] and rows[0] != SOURCE


def test_owner_needs_its_residual_evidence():
    assert not proposals(diff="")
    stripped = "\n".join(line for line in DIFF.splitlines() if not line.startswith(("-", "+")) or
                         line.startswith(("---", "+++")))
    assert not proposals(diff=stripped)


def test_every_listed_owner_exists_and_is_not_already_exposed():
    exposed = {"byte_pointer_step_rewrites", "pointer_element_width_rewrites", "pointer_difference_scale_rewrites",
               "compare_swap_rewrites", "branch_sentinel_rewrites", "signed_compare_rewrites",
               "inline_temporary_rewrites", "statement_order_rewrites", "do_while_restore_rewrites"}
    for name in owner_rewrites.OWNERS:
        assert callable(getattr(rewrites, name))
        assert name not in exposed
    assert len(set(owner_rewrites.OWNERS)) == len(owner_rewrites.OWNERS)


def test_a_raising_owner_declines_its_own_family_only(monkeypatch):
    def broken(code, diff):
        raise KeyError("boom")
    monkeypatch.setattr(rewrites, "argswap_rewrites", broken)
    with pytest.raises(owner_rewrites.OwnerRaised):
        list(owner_rewrites.candidates("argswap_rewrites", SOURCE, DIFF))
    assert len(proposals()) == 1                      # the layout owner still fires beside it


def test_owner_candidates_never_edit_preprocessor_lines(monkeypatch):
    class Rewrite:
        label = "drop-include"

        def __call__(self, code):
            return code.replace('#include "common.h"\n', "")
    monkeypatch.setattr(rewrites, "argswap_rewrites", lambda code, diff: [Rewrite()])
    assert not list(owner_rewrites.candidates("argswap_rewrites", SOURCE, DIFF))
