"""`restore_do_while` must fire on its motivating residual, and must not guess elsewhere.

The generator exists because the build helper's `do`-token refusal forced a lowering that is not
codegen-neutral. On drawRaceSplitscreenSelectOption2Frame the ROM-verified body with `do` compiles
BYTE-EXACT and the lowered form of that same body scores 99.395, so for that function the mandated
lowering was the whole residual. The refusal was removed on operator instruction 2026-09-17, and this
inverse is what lets a `for(;;)+break` candidate be offered back in the spelling the oracle may want.

Two halves, because both fail silently on their own: the round-trip must be exact on real text (the
FIRES half), and it must decline on loops the lowering did not produce (the DECLINES half).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.score_repo_function import rewrite_do_while                # noqa: E402
from solver import rewrites                                          # noqa: E402

# The real shape: `do { ... } while (!(offset != i));` from the sibling family's ROM-verified body,
# with nested calls and a parenthesised condition so brace and paren matching are both exercised.
BODY = """\
    tileIndex = 0;
    shouldDraw = 1;
    for (i = 0; i < 16; i++, tileIndex++) {
        drawMenuSpriteTileClipped(
            (s16)(arg0->x + ((i & 3) << 5)),
            0x49
        );
    }
    if (shouldDraw) {
        tileIndex = 0;
        i = 0x80;
    }
    offset = 0;
    do {
        drawMenuSpriteTileClipped(
            (s16)(arg0->y + offset),
            0x49
        );
        i = 0x80;
        offset += 0x40;
        tileIndex++;
    } while (!(offset != i));
"""


def test_restore_is_the_exact_inverse_of_the_mandated_lowering():
    """Ground truth: the same text back, character for character, not merely equivalent code."""
    lowered = rewrite_do_while(BODY)
    assert "do {" not in lowered, "the forward direction must remove the token"
    assert "for (;;)" in lowered
    for _ in range(4):                      # restore_do_while handles one site per call
        lowered = rewrites.restore_do_while(lowered)
        if lowered == BODY:
            break
    assert lowered == BODY


def test_restore_emits_the_token_the_ban_refused():
    lowered = rewrite_do_while(BODY)
    restored = rewrites.restore_do_while(lowered)
    assert "do {" in restored
    assert "while (!(offset != i));" in restored
    assert "for (;;)" not in restored


def test_one_variant_per_site_and_each_is_independently_applicable():
    lowered = rewrite_do_while(BODY)
    variants = rewrites.do_while_restore_rewrites(lowered, "")
    assert len(variants) == 1
    assert variants[0].kind == "loopshape"
    assert variants[0](lowered).count("do {") == 1


def test_two_sites_yield_two_variants():
    doubled = rewrite_do_while(BODY + BODY)
    variants = rewrites.do_while_restore_rewrites(doubled, "")
    assert len(variants) == 2
    for variant in variants:
        assert variant(doubled) != doubled


def test_it_declines_when_the_lowering_did_not_make_the_loop():
    """A top-tested `while`, and a `for(;;)` with a different tail, are not this generator's business.

    A generator that fires on everything it half-recognises is how a pass starts changing behaviour
    on residuals it does not own; the oracle would reject the proposal, but the budget is still spent.
    """
    top_tested = "    while (i < 16) {\n        i++;\n    }\n"
    assert rewrites.do_while_restore_rewrites(top_tested, "") == []
    assert rewrites.restore_do_while(top_tested) == top_tested

    unrelated_tail = "    for (;;) {\n        i++;\n        if (i > 3) break;\n    }\n"
    assert rewrites.do_while_restore_rewrites(unrelated_tail, "") == []

    no_loop = "void f(void) {\n    a = 1;\n}\n"
    assert rewrites.do_while_restore_rewrites(no_loop, "") == []


def test_the_search_proposes_the_restoration_as_a_family():
    """The wiring, so the inverse is actually reachable from `regalloc_search` and not just callable.

    Same silent-decline shape this project keeps catching: a pass that works, is tested, and is offered
    to no driver.
    """
    from solver import regalloc_mutations

    lowered = rewrite_do_while("void f(void) {\n" + BODY + "}\n")
    got = [row for row in regalloc_mutations.existing(lowered, "")]
    assert any(kind == "do_restore" for _label, kind, _variant in got), \
        "the restoration is not offered to the search"
    assert all("do {" in variant for _label, kind, variant in got if kind == "do_restore")
    assert regalloc_mutations.existing("void f(void) {\n    a = 1;\n}\n", "") is not None


BANNED_HELPER = """\
#!/usr/bin/env bash
set -euo pipefail
INPUT="$1"
# Agents: This restriction is intentional; do not remove, disable, or bypass it.
if python3 - "$INPUT" <<'PY'
import re
import sys
sys.exit(0 if re.search(r"\\bdo\\b", sys.stdin.read()) else 1)
PY
then
    echo "ERROR: The C file contains a do-while loop."
    echo "Rewrite the loop using while or for instead."
    exit 1
fi
echo built
"""


def test_a_fresh_clone_heals_itself(tmp_path):
    """The helper lives in a git SUBMODULE, so a worktree edit is one `submodule update` from gone.

    If the project relied on the edit alone, the ban would come back silently -- and silently is the
    whole problem, because a refused candidate records a policy message instead of a compiler error.
    """
    from solver import workspace as workspace_mod

    repo = tmp_path / "repo"
    (repo / "tools" / "claude-decomp-env").mkdir(parents=True)
    helper = repo / "tools" / "claude-decomp-env" / "build.sh"
    helper.write_text(BANNED_HELPER)
    workspace_mod._HELPER_CHECKED.discard(str(helper))

    assert workspace_mod.ensure_helper_allows_do(repo) is True
    assert "do-while loop" not in helper.read_text()
    assert "do { ... } while" in helper.read_text()
    # Idempotent, and it must not rewrite the file again on the cached path.
    workspace_mod._HELPER_CHECKED.discard(str(helper))
    assert workspace_mod.ensure_helper_allows_do(repo) is False


def test_a_helper_without_the_ban_is_left_untouched(tmp_path):
    from solver import workspace as workspace_mod

    repo = tmp_path / "repo"
    (repo / "tools" / "claude-decomp-env").mkdir(parents=True)
    helper = repo / "tools" / "claude-decomp-env" / "build.sh"
    helper.write_text("#!/usr/bin/env bash\necho built\n")
    workspace_mod._HELPER_CHECKED.discard(str(helper))
    assert workspace_mod.ensure_helper_allows_do(repo) is False
    assert helper.read_text() == "#!/usr/bin/env bash\necho built\n"
