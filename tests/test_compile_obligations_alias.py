r"""The alias repair in `solver/compile_obligations.opaque_variant`, both directions.

THE DEFECT. `opaque_variant` decided whether a header already gives a name to a type with
`re.search(r'\btypedef\b[^;]*\bNAME\s*;', header_text)`. `[^;]*` cannot cross a semicolon, so the check
could not see `typedef struct RacePlayer { s32 speed; } RacePlayer;` — the way every real struct is
written — and appended `typedef struct RacePlayer RacePlayer;` anyway. cfe answered
`redeclaration of 'RacePlayer'; previous declaration at line 243 in race_player_input.h` and a compiling
candidate became an uncompilable one. Measured on the 17-state development panel: 3 fires, 3 destroyed.

THE OTHER DIRECTION, which the fix must not break: `struct RacePlayer;` alone is a TAG, not an alias, so
`RacePlayer` is not a type name yet — that is precisely the case this repair exists for, and the check has
to tell the two apart.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solver import compile_obligations                                       # noqa: E402

FUNCTION = "updateRacePlayerLeanAngle"
# Verbatim shape of the candidate: m2c dropped the `struct` keyword, the header supplies the type.
SOURCE = "void updateRacePlayerLeanAngle(RacePlayer *arg0) {\n    arg0->unk4 = 1;\n}\n"
FULL_TYPEDEF = "typedef struct RacePlayer {\n    /* 0x0 */ s32 speed;\n} RacePlayer;"
TAG_ONLY = "struct RacePlayer;"


def test_no_alias_is_emitted_when_the_header_already_typedefs_the_name():
    """The motivating case. Before the fix this returned one declaration, and that declaration was the
    redeclaration the compiler rejected."""
    assert compile_obligations.missing_alias_declarations(SOURCE, FUNCTION, FULL_TYPEDEF) == []


def test_the_same_name_given_anonymously_is_also_seen():
    """`typedef struct { ... } RacePlayer;` puts the name LAST. A check for `typedef\\s+RacePlayer` misses
    it — the same shape as the bug `typedecl.declared_in` was written to fix."""
    assert compile_obligations.missing_alias_declarations(
        SOURCE, FUNCTION, "typedef struct { s32 speed; } RacePlayer;") == []


def test_a_tag_only_header_still_gets_the_alias():
    """THE REPAIR, PRESERVED. `struct RacePlayer;` makes `struct RacePlayer` usable and leaves `RacePlayer`
    unusable, which is what m2c wrote."""
    assert compile_obligations.missing_alias_declarations(SOURCE, FUNCTION, TAG_ONLY) == [
        {"type": "RacePlayer", "text": "typedef struct RacePlayer RacePlayer;"}]


def test_a_name_the_source_itself_declares_is_left_alone():
    source = "typedef struct RacePlayer RacePlayer;\n" + SOURCE
    assert compile_obligations.missing_alias_declarations(source, FUNCTION, TAG_ONLY) == []


def test_a_typedef_of_a_different_name_does_not_suppress_the_alias():
    """`RacePlayerHandle` is a type name; `RacePlayer` is not. The header forwards the tag, so the source
    cannot spell `RacePlayer *arg0` without the `struct` keyword."""
    header = "struct RacePlayer;\ntypedef struct RacePlayer RacePlayerHandle;"
    assert compile_obligations.missing_alias_declarations(SOURCE, FUNCTION, header) == [
        {"type": "RacePlayer", "text": "typedef struct RacePlayer RacePlayer;"}]


def test_a_tag_defined_only_with_a_body_is_not_repaired_and_that_is_a_coverage_limit():
    """`bare_tags` is collected from `struct X;` forward declarations, so a header that only ever writes
    `struct X { ... } Alias;` is not seen here and no alias is offered.

    Stated rather than silently accepted: this pass declines on that shape, it does not damage it, and
    widening `bare_tags` would change which states the action fires on -- which needs a measurement, not a
    guess. Recorded so the next reader does not have to rediscover it.
    """
    header = "typedef struct RacePlayer { s32 speed; } RacePlayerHandle;"
    assert compile_obligations.missing_alias_declarations(SOURCE, FUNCTION, header) == []


def test_the_emitter_refuses_a_declaration_that_would_redeclare():
    """The belt-and-braces guard, which is decidable without a compiler: the text about to be INSERTED
    must not name a type the translation unit already has."""
    header = "typedef struct RacePlayer { s32 speed; } RacePlayer;"
    added = [{"type": "RacePlayer", "text": "typedef struct RacePlayer RacePlayer;"}]
    assert "redeclare the type name 'RacePlayer'" in compile_obligations._redeclaration(
        added, "void f(void) {}\n", header)
    assert compile_obligations._redeclaration(added, "void f(void) {}\n", TAG_ONLY) == ""


def test_a_struct_tag_that_is_already_defined_is_refused_too():
    header = "typedef struct RacePlayer { s32 speed; } RacePlayer;"
    assert "redefine the struct tag 'RacePlayer'" in compile_obligations._redeclaration(
        [{"text": "struct RacePlayer {\n    s32 speed;\n};"}], "", header)


def test_the_guard_accepts_a_genuinely_new_declaration():
    assert compile_obligations._redeclaration(
        [{"text": "typedef struct {\n    s32 unk4;\n} updateRacePlayerLeanAngle_arg0;"}],
        "void f(void) {}\n", "") == ""
