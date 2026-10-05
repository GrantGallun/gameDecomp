"""Context/body separation: split exactly, assemble back, key on real bytes, discard non-body."""
from __future__ import annotations

from pathlib import Path

import pytest

from solver import compile_context as cc

SOURCE = ('#include "common.h"\n#include "game/actor.h"\n\n'
          "extern s32 gCount;\n\n"
          "void f(Actor *a) {\n    a->x = gCount;\n}\n\n"
          "/* trailing */\n")


def test_split_and_assemble_round_trip() -> None:
    sp = cc.split(SOURCE, "f")
    assert sp.body.startswith("void f(Actor *a) {") and sp.body.endswith("}")
    assert "extern s32 gCount;" in sp.context and "a->x" not in sp.context
    assert sp.assemble() == SOURCE.replace(sp.body, sp.body.strip())
    swapped = sp.assemble("void f(Actor *a) {\n    a->x = 0;\n}")
    assert "a->x = 0;" in swapped and swapped.startswith('#include "common.h"')
    with pytest.raises(ValueError):
        cc.split("int x;\n", "f")


def test_stub_validates_the_context_alone() -> None:
    assert cc.stub_body("void f(Actor *a) {\n    a->x = 1;\n}") == "void f(Actor *a) {\n}\n"


def test_key_changes_with_included_header_bytes(tmp_path: Path) -> None:
    (tmp_path / "include" / "game").mkdir(parents=True)
    (tmp_path / "include" / "common.h").write_text('#include "game/actor.h"\n')
    header = tmp_path / "include" / "game" / "actor.h"
    header.write_text("typedef struct { s32 x; } Actor;\n")
    context = cc.split(SOURCE, "f").context
    files = cc.included_files(tmp_path, context)
    assert [p.name for p in files] == ["common.h", "actor.h"], "transitive, in include order"
    before = cc.key(tmp_path, context, recipe="-O2")
    assert before == cc.key(tmp_path, context, recipe="-O2")
    header.write_text("typedef struct { s16 x; } Actor;\n")
    assert cc.key(tmp_path, context, recipe="-O2") != before, "a header byte change is a new context"
    assert cc.key(tmp_path, context, recipe="-O1") != cc.key(tmp_path, context, recipe="-O2")


def test_extract_body_keeps_only_the_target_definition() -> None:
    answer = ("<think>```c\nvoid f(Actor *a) { scratch(); }\n```</think>\n```c\n"
              "typedef struct { s32 y; } Actor;\nextern s32 gOther;\n"
              "static void helper(void) {}\n"
              "void f(Actor *a) {\n    a->x = gCount;\n}\n```")
    body = cc.extract_body(answer, "f")
    assert body.startswith("void f(Actor *a) {") and "gCount" in body
    assert "typedef" not in body and "helper" not in body and "scratch" not in body
    assert cc.extract_body("no code here", "f") == ""


def test_body_prompt_shows_context_read_only_and_asks_for_the_definition_only() -> None:
    sp = cc.split(SOURCE, "f")
    prompt = cc.body_prompt("f", sp, "glabel f", 80.0, "-lw a0,0(v0)")
    assert "Write ONLY the complete definition of `f`" in prompt
    assert "/* @@BODY@@ */" in prompt and "extern s32 gCount;" in prompt
    assert "-lw a0,0(v0)" in prompt and "80.000" in prompt


def test_undeclared_flags_only_names_nobody_declares(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "common.h").write_text(
        "typedef signed short s16;\ntypedef struct { s16 x; } Actor;\n"
        "extern Actor gRaceStateData;\nvoid drawActor(Actor *a);\n#define MAX_ACTORS 8\n"
        "enum Mode { MODE_IDLE, MODE_RUN = 3 };\n")
    context = '#include "common.h"\n/* @@BODY@@ */\n'
    known = cc.symbols(tmp_path, context)
    assert {"s16", "Actor", "gRaceStateData", "drawActor", "MAX_ACTORS", "MODE_RUN"} <= known
    body = ("void f(Actor *a, s16 n) {\n    s16 i;\n    Actor *p = &gRaceState;\n"
            "    for (i = 0; i < MAX_ACTORS; i++) { a->x = n; drawActor(p); }\n"
            "    if (n) goto done;\n    missingCall(a);\ndone:\n    p->y = \"text x\"[0];\n}\n")
    flagged = cc.undeclared(body, known)
    names = [f["name"] for f in flagged]
    # missingCall(a) is a CALL: C89 declares it implicitly and IDO accepts it
    assert names == ["gRaceState"], "members, labels, locals, strings, calls are not flagged"
    assert flagged[0]["closest"][0] == "gRaceStateData"
    assert cc.undeclared("void f(void) {\n    drawActor(0);\n}\n", known) == []


def test_local_declarations_are_read_precisely() -> None:
    body = ("void f(Actor *a, s16 n) {\n    s16 i, *q = 0, arr[4];\n    struct Foo foo;\n"
            "    const u8 k = n;\n    i = n;\n    use(q);\n    return i;\n}\n")
    # the definition declares the function itself too
    assert cc.local_declarations(body) == {"f", "a", "n", "i", "q", "arr", "foo", "k"}


def test_numeric_literals_are_not_identifiers() -> None:
    """The probe's first finding: `0x18` read as `x18`, `1U` as `U`."""
    body = "void f(s16 *p) {\n    p[0x18] = 1U + 0xFFF58000 + 2.5f + 3L;\n}\n"
    assert cc.undeclared(body, frozenset({"s16"})) == []


def test_includes_resolve_on_the_project_include_path(tmp_path: Path) -> None:
    (tmp_path / "include" / "PR").mkdir(parents=True)
    (tmp_path / "include" / "common.h").write_text("#include <os_internal.h>\n")
    (tmp_path / "include" / "PR" / "os_internal.h").write_text("typedef struct { int a; } OSPifRam;\n")
    files = cc.included_files(tmp_path, '#include "common.h"\n')
    assert [p.name for p in files] == ["common.h", "os_internal.h"]
    assert "OSPifRam" in cc.symbols(tmp_path, '#include "common.h"\n')
