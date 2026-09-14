from pathlib import Path

from solver import project_headers


def _repo(tmp_path: Path) -> Path:
    include = tmp_path / "include" / "game" / "audio"
    include.mkdir(parents=True)
    (include / "scheduler.h").write_text(
        "typedef struct SchedulerState SchedulerState;\n"
        "OSMesgQueue *getQueue(\n"
        "    SchedulerState *scheduler);\n"
        "void noopCallback(void *task);\n",
        encoding="utf-8")
    return tmp_path


def test_finds_multiline_project_declaration(tmp_path: Path):
    found = project_headers.declarations(_repo(tmp_path), "getQueue")

    assert found == [project_headers.HeaderDeclaration(
        "game/audio/scheduler.h",
        "OSMesgQueue *getQueue( SchedulerState *scheduler)")]


def test_adds_declaring_header_to_m2c_draft(tmp_path: Path):
    repo = _repo(tmp_path)
    draft = '#include "common.h"\n\nvoid getQueue(void) {}\n'

    variants = project_headers.preflight_variants(
        repo, "getQueue", "glabel getQueue\njr $ra\nnop\nendlabel getQueue",
        draft)

    assert variants[0][0] == "project_header:game/audio/scheduler.h"
    assert '#include "game/audio/scheduler.h"' in variants[0][1]


def test_prompt_context_contains_headers_but_no_source_tree(tmp_path: Path):
    repo = _repo(tmp_path)

    context = project_headers.prompt_context(
        repo, "getQueue", "glabel getQueue\njr $ra\nnop")

    assert "HEADER game/audio/scheduler.h:" in context
    assert "typedef struct SchedulerState" in context
    assert "src/" not in context


def test_prompt_context_prioritizes_real_global_and_type_declarations(
        tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "include" / "world.h").write_text(
        "typedef struct WorldState {\n"
        "    /* 0x0 */ u8 active;\n"
        "    /* 0x4 */ s32 score;\n"
        "} WorldState;\n"
        "extern WorldState gWorldStates[4];\n",
        encoding="utf-8")
    draft = (
        "extern struct WorldState *gWorldStates;\n"
        "void update(void) { gWorldStates->active = 0; }\n")

    context = project_headers.prompt_context(
        repo, "getQueue", "glabel getQueue\njr $ra\nnop", draft)

    assert "extern WorldState gWorldStates[4];" in context
    assert "definition of WorldState" in context
    assert "/* 0x4 */ s32 score;" in context


def test_synthesizes_declared_empty_definition(tmp_path: Path):
    repo = _repo(tmp_path)
    asm = """glabel noopCallback
    jr $ra
     nop
endlabel noopCallback
"""

    variants = project_headers.preflight_variants(
        repo, "noopCallback", asm, '#include "common.h"\n')

    label, source = variants[-1]
    assert label == "binary_empty_return:game/audio/scheduler.h"
    assert "void noopCallback(void *task) {\n}" in source


def test_does_not_synthesize_for_nonempty_function(tmp_path: Path):
    repo = _repo(tmp_path)
    asm = "glabel noopCallback\njr $ra\naddiu $v0, $a0, 4\nendlabel noopCallback\n"

    variants = project_headers.preflight_variants(
        repo, "noopCallback", asm, '#include "common.h"\n')

    assert all(not label.startswith("binary_empty_return")
               for label, _source in variants)


def test_adds_direct_callee_declaration_headers(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "include" / "callee.h").write_text(
        "s32 calculateValue(s32 input);\n", encoding="utf-8")
    asm = """glabel getQueue
    jal calculateValue
     nop
    jr $ra
     nop
endlabel getQueue
"""

    assert project_headers.called_functions(asm) == ["calculateValue"]
    variants = project_headers.preflight_variants(
        repo, "getQueue", asm, '#include "common.h"\n')
    context = dict(variants)["project_call_context:2"]
    assert '#include "game/audio/scheduler.h"' in context
    assert '#include "callee.h"' in context


def test_adds_headers_for_used_type_definitions_and_globals(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "include" / "world.h").write_text(
        "typedef struct WorldState { s32 value; } WorldState;\n"
        "extern u8 gWorldPaused;\n", encoding="utf-8")
    draft = """#include "common.h"
void update(WorldState *world) {
    if (gWorldPaused) world->value = 0;
}
"""

    assert project_headers.dependency_headers(repo, draft) == ["world.h"]


def test_adds_header_for_callback_passed_as_bare_argument(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "include" / "callbacks.h").write_text(
        "void emitParticle(void *actor);\n", encoding="utf-8")
    draft = """#include "common.h"
void update(void *actor) {
    scheduleCallback(emitParticle, 5, actor);
}
"""

    assert project_headers.dependency_headers(repo, draft) == ["callbacks.h"]
