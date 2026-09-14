import json
import sqlite3

import pytest

from eval import prepare_integration as prep


def test_function_only_replacement_preserves_neighbors_and_handles_comments():
    source = '#include "common.h"\nint before(void) {return 2;}\nint f(int x) { /* } */ return x; }\nint after(void) {return 3;}\n'
    candidate = '#include "common.h"\n/* draft */\nint f(int x) { return x + 0; }\n'
    actual = prep.replace_function(source, candidate, "f")
    assert actual == source.replace('int f(int x) { /* } */ return x; }', 'int f(int x) { return x + 0; }')


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
