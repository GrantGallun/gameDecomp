import json
import sqlite3

import pytest

from eval import prepare_integration as prep


def test_function_only_replacement_preserves_neighbors_and_handles_comments():
    source = '#include "common.h"\nint before(void) {return 2;}\nint f(int x) { /* } */ return x; }\nint after(void) {return 3;}\n'
    candidate = '#include "common.h"\n/* draft */\nint f(int x) { return x + 0; }\n'
    actual = prep.replace_function(source, candidate, "f")
    assert actual == source.replace('int f(int x) { /* } */ return x; }', 'int f(int x) { return x + 0; }')


def test_extern_obligations_preserved_at_replaced_function():
    # Motivating saved osCreateMesgQueue source has an actual extern object;
    # fadeOutMultiplayerCourseSelectMenu also has a zero-argument prototype.
    original = '#include "common.h"\nint before(void) {return 2;}\nint f(void) {return 0;}\n'
    candidate = '#include "common.h"\nextern OSThread __osThreadTail;\nextern void releaseMenuAssetHandles(void);\nint f(void) {return 1;}\n'
    actual = prep.replace_function(original, candidate, 'f')
    assert actual == original.replace('int f(void) {return 0;}',
        'extern OSThread __osThreadTail;\nextern void releaseMenuAssetHandles(void);\nint f(void) {return 1;}')
    # Intake cannot treat an unprobed extern as equivalent to real headers.
    with pytest.raises(ValueError, match='contextual frontend'):
        prep.candidate_parts(candidate, 'f')


def test_plain_prototypes_become_extern_declarations():
    # addMainMenuSceneModelDrawCallback (object-exact, 2026-09-14) was blocked only by this line.
    candidate = ('#include "common.h"\nvoid drawMainMenuSceneModel(void *);\nstruct Foo *makeFoo(s32, u8 (*)(void));\n'
                 'extern s32 gCount;\nvoid f(void) { drawMainMenuSceneModel(0); }\n')
    _body, includes, declarations = prep._candidate_components(candidate, 'f')
    assert includes == ['common.h']
    assert declarations == ['extern s32 gCount;', 'extern void drawMainMenuSceneModel(void *);',
                            'extern struct Foo *makeFoo(s32, u8 (*)(void));']


def test_converted_prototype_yields_to_a_destination_declaration_but_explicit_externs_stay():
    original = ('#include "common.h"\nvoid f(void) {}\n'
                'void initTrainingCourseEndingDialog(TrainingCourseUiActor *arg0) {}\n')
    candidate = ('void initTrainingCourseEndingDialog(void *);\nvoid helperUnknownToTu(void);\nextern s16 gMenuFadeAlpha;\n'
                 'void f(void) { initTrainingCourseEndingDialog(0); helperUnknownToTu(); }\n')
    replaced = prep.replace_function(original, candidate, 'f')
    # Defined later in the destination TU: the m2c guess would conflict, so it yields.
    assert 'extern void initTrainingCourseEndingDialog(void *);' not in replaced
    # Unknown to the destination: the candidate's declaration is still needed; explicit externs stay.
    assert 'extern s16 gMenuFadeAlpha;\nextern void helperUnknownToTu(void);\nvoid f(void) {' in replaced


@pytest.mark.parametrize('extra', ['static void helper(void);', 'void (*callback)(void);',
                                   'typedef void handler(void);', 'struct Foo { int x; };'])
def test_non_prototype_declarations_still_block(extra):
    with pytest.raises(ValueError, match='shared declaration'):
        prep._candidate_components(extra + '\nvoid f(void) {}\n', 'f')


@pytest.fixture
def extern_frontend(tmp_path, monkeypatch):
    import shutil
    from solver import frontend_check
    clang = shutil.which('clang')
    if not clang:
        pytest.skip('real Clang declaration AST required')
    (tmp_path / 'Makefile').write_text('fixture')
    (tmp_path / 'types.h').write_text('typedef struct { int state; } OSThread;\n')
    monkeypatch.setattr(frontend_check, 'recipe', lambda *args:
        {'command': [clang, '-fsyntax-only', '-std=gnu89', '-Werror', '-I', str(tmp_path)],
         'checker_sha256': prep.sha(__import__('pathlib').Path(clang).read_bytes())})
    return tmp_path


def test_real_frontend_admits_plain_extern_objects_and_prototypes(extern_frontend):
    declarations = ['extern OSThread __osThreadTail;', 'extern void releaseMenuAssetHandles(void);',
                    'extern signed char gFramebufferSwapHold;', 'extern unsigned char gPendingFramebufferSwapCount;']
    report = prep.check_extern_declarations(repo=extern_frontend, target='build/f.o',
                                           includes=['types.h'], declarations=declarations)
    assert [r['name'] for r in report['declarations']] == [
        '__osThreadTail', 'releaseMenuAssetHandles', 'gFramebufferSwapHold', 'gPendingFramebufferSwapCount']
    assert [r['kind'] for r in report['declarations']] == ['VarDecl', 'FunctionDecl', 'VarDecl', 'VarDecl']


@pytest.mark.parametrize('declaration', [
    'extern int value = 1;', 'static int value;', 'typedef int value;',
    'extern int a, b;', 'extern inline int f(void);', 'extern int f(void) {return 1;}',
    'extern int value __attribute__((aligned(32)));', 'extern Unknown value;',
])
def test_real_frontend_declines_nonordinary_externs(extern_frontend, declaration):
    with pytest.raises(ValueError):
        prep.check_extern_declarations(repo=extern_frontend, target='build/f.o',
                                       includes=['types.h'], declarations=[declaration])


def test_extern_macro_expansion_and_header_type_conflict_decline(extern_frontend):
    (extern_frontend / 'types.h').write_text('#define GENERATED hidden\nextern int known;\n')
    for declaration in ['extern int GENERATED;', 'extern float known;']:
        with pytest.raises(ValueError):
            prep.check_extern_declarations(repo=extern_frontend, target='build/f.o',
                                           includes=['types.h'], declarations=[declaration])


def test_unrelated_conditionals_preserved_but_conditional_definition_refused():
    original = '#ifdef DEBUG\nint debug;\n#endif\nint f(void) {return 0;}\n'
    candidate = 'int f(void) {return 1;}\n'
    assert prep.replace_function(original, candidate, "f") == original.replace('return 0;', 'return 1;')
    with pytest.raises(ValueError, match="conditional function"):
        prep.replace_function('#ifdef DEBUG\nint f(void) {return 0;}\n#endif\n', candidate, "f")


@pytest.mark.parametrize("candidate", [
    "int global;\nint f(void) {return 1;}",
    "int f(void) { asm(\"x\"); }",
    "int f(void) {return 1;}\nint f(void) {return 2;}",
    "#define RET 1\nint f(void) {return RET;}"])
def test_unsupported_candidates_declined(candidate):
    with pytest.raises(ValueError):
        prep.candidate_parts(candidate, "f")


def test_source_and_attempt_bound_preparation(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    original = repo / "src/f.c"
    original.write_text("int f(void) {return 0;}\n")
    (repo / "snowboardkids.z64").write_bytes(b"ROM")
    candidate = tmp_path / "candidate.c"
    candidate.write_text("int f(void) {return 1;}\n")
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("CREATE TABLE functions(name,addr,tu_id); CREATE TABLE tus(id,name); "
                           "CREATE TABLE attempts(id,func_addr,source_code);")
        conn.execute("INSERT INTO functions VALUES ('f',1,1)")
        conn.execute("INSERT INTO tus VALUES(1,'build/src/f.o')")
        conn.execute("INSERT INTO attempts VALUES(1,1,?)", (candidate.read_text(),))
    entry = {"function": "f", "attempt_id": 1, "source": str(candidate),
             "verification": {"exact": True, "source_sha256": "compiler-prepared-source",
                              "candidate_source_sha256": prep.sha(candidate.read_text().encode())}}
    manifest = prep.prepare(repo=repo, db=db, entries=[entry], output_dir=tmp_path / "prepared")
    assert json.loads(manifest.read_text())["lineage"][0]["attempt_id"] == 1
    assert original.read_text() == "int f(void) {return 0;}\n"
    candidate.write_text("int f(void) {return 2;}\n")
    with pytest.raises(ValueError, match="source-bound"):
        prep.prepare(repo=repo, db=db, entries=[entry], output_dir=tmp_path / "stale")


ACTOR = ('typedef struct ExampleActor {\n    /* 0x00 */ u8 pad0[0x18];\n    /* 0x18 */ s16 alpha;\n} ExampleActor;\n')


def test_candidate_typedefs_and_macros_are_carried_into_the_destination():
    # Fires on the 2026-09-30 residual: func_8005CF60 / func_8005C14C were function-exact but
    # blocked only by a local `typedef struct ... Actor;` and `#define label D_800E...` aliases.
    original = '#include "common.h"\nint before(void) {return 2;}\nvoid f(void) {}\n'
    candidate = ('#include "common.h"\n' + ACTOR + '#define gLabel D_800E139C\nextern char D_800E139C[];\n'
                 'void f(ExampleActor *a) { a->alpha = gLabel[0]; }\n')
    body, includes, declarations, preamble = prep._candidate_split(candidate, 'f')
    assert [b.split('\n')[0] for b in preamble] == ['typedef struct ExampleActor {', '#define gLabel D_800E139C']
    replaced = prep.replace_function(original, candidate, 'f')
    assert replaced.index('typedef struct ExampleActor') < replaced.index('extern char D_800E139C[];') < replaced.index('void f(ExampleActor')
    assert replaced.count('ExampleActor;') == 1 and 'int before(void) {return 2;}' in replaced
    # the context-free readiness check still refuses: admission needs the frontend and the ROM gate
    for path in (prep._candidate_components, prep.candidate_parts):
        with pytest.raises(ValueError, match='shared declaration'):
            path(candidate, 'f')


def test_identical_preamble_yields_and_a_second_function_does_not_redefine_it():
    candidate = '#include "common.h"\n' + ACTOR + 'void f(ExampleActor *a) { a->alpha = 1; }\n'
    other = '#include "common.h"\n' + ACTOR + 'void g(ExampleActor *a) { a->alpha = 2; }\n'
    original = '#include "common.h"\nvoid f(void) {}\nvoid g(void) {}\n'
    once = prep.replace_function(original, candidate, 'f')
    twice = prep.replace_function(once, other, 'g')
    assert twice.count('} ExampleActor;') == 1
    assert prep.replace_function(once, candidate, 'f') == once        # the destination already states it


def test_same_name_different_definition_declines_rather_than_merging():
    original = '#include "common.h"\ntypedef struct { s32 alpha; } ExampleActor;\n#define gLabel 5\nvoid f(void) {}\n'
    with pytest.raises(ValueError, match='conflicts'):
        prep.replace_function(original, '#include "common.h"\n' + ACTOR + 'void f(ExampleActor *a) {}\n', 'f')
    with pytest.raises(ValueError, match='conflicts'):
        prep.replace_function(original, '#define gLabel 6\nvoid f(void) { gLabel; }\n', 'f')


@pytest.mark.parametrize('extra', ['typedef struct Foo *FooPtr;', 'typedef int Vec[3];',
                                   'typedef struct { int x; } A, *B;', '#define MACRO(x) ((x) + 1)',
                                   '#define STR "text"', 'typedef struct { int x; } Open'])
def test_unadmitted_file_scope_shapes_still_block(extra):
    with pytest.raises(ValueError, match='shared declaration'):
        prep._candidate_components(extra + '\nvoid f(void) {}\n', 'f')
    with pytest.raises(ValueError):
        prep.replace_function('void f(void) {}\n', extra + '\nvoid f(void) {}\n', 'f')


def test_frontend_probe_sees_the_preamble_so_prototypes_over_local_types_admit(extern_frontend):
    # updateEndingObjectSpriteDebugViewer: `void draw...(LocalActor *);` names a type only the candidate defines.
    preamble = ['typedef struct A { int x; } LocalActor;']
    report = prep.check_extern_declarations(repo=extern_frontend, target='build/f.o', includes=['types.h'],
                                            declarations=['extern void draw(LocalActor *);'], preamble=preamble)
    assert [r['name'] for r in report['declarations']] == ['draw']
    with pytest.raises(ValueError):
        prep.check_extern_declarations(repo=extern_frontend, target='build/f.o', includes=['types.h'],
                                       declarations=['extern void draw(LocalActor *);'])
    with pytest.raises(ValueError):   # a preamble that clashes with a header type is caught before the ROM build
        prep.check_extern_declarations(repo=extern_frontend, target='build/f.o', includes=['types.h'],
                                       declarations=[], preamble=['typedef struct { int y; } OSThread;'])


def test_tu_owned_string_objects_are_admitted_as_data_and_yield_or_decline_on_name():
    # Fires on the 2026-09-30 residual: func_8005A884 / func_8005CF60 / func_8005C14C define
    # `const char gRaceUi...Format[4] = "%d";` at file scope. The text is data, so placement is left to the ROM gate.
    candidate = ('#include "common.h"\nconst char gFmt[4] = "%4d";\nconst char gLabel[0xC] = "a \\"b\\" c";\n'
                 'void f(void) { puts(gFmt); puts(gLabel); }\n')
    _body, _includes, _declarations, preamble = prep._candidate_split(candidate, 'f')
    assert preamble == ['const char gFmt[4] = "%4d";', 'const char gLabel[0xC] = "a \\"b\\" c";']
    replaced = prep.replace_function('#include "common.h"\nvoid f(void) {}\n', candidate, 'f')
    assert replaced.index('const char gFmt[4]') < replaced.index('const char gLabel') < replaced.index('void f(void) { puts')
    assert prep.replace_function(replaced, candidate, 'f') == replaced         # identical: yields, not duplicated
    with pytest.raises(ValueError, match='conflicts'):
        prep.replace_function('extern char gFmt[];\nvoid f(void) {}\n', candidate, 'f')
    with pytest.raises(ValueError, match='shared declaration'):
        prep._candidate_components(candidate, 'f')


@pytest.mark.parametrize('extra', ['const char gFmt[4] = "%4d" "x";', 'char gFmt[4] = "%4d";', 'const s32 gTable[2] = {1, 2};',
                                   'const char *gFmt = "%4d";', 'const char gFmt[4] = "%4d", gOther[2] = "a";'])
def test_other_file_scope_data_still_blocks(extra):
    with pytest.raises(ValueError, match='shared declaration'):
        prep._candidate_split(extra + '\nvoid f(void) {}\n', 'f')
