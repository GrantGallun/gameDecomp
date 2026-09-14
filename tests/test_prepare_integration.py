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
