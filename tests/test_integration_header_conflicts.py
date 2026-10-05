"""Actual-header conflicts must be measured without supplying reference bodies."""
import shutil
import json
import sqlite3

import pytest

from eval import prepare_integration as pi
from solver import frontend_check


@pytest.fixture
def header_repo(tmp_path, monkeypatch):
    clang = shutil.which('clang')
    if not clang:
        pytest.skip('real Clang AST required')
    (tmp_path / 'Makefile').write_text('fixture')
    (tmp_path / 'api.h').write_text('extern int input[4];\nextern unsigned char flag;\nextern int same;\n')
    monkeypatch.setattr(frontend_check, 'recipe', lambda *args:
        {'command': [clang, '-fsyntax-only', '-std=gnu89', '-Werror', '-I', str(tmp_path)]})
    return tmp_path


def test_measured_header_array_conflict_fires_and_preserves_candidate_scalar_access(header_repo):
    declarations = ['extern int input;']
    admission = pi.check_extern_declarations(repo=header_repo, target='build/f.o',
                                           includes=[], declarations=declarations)
    report = pi.header_object_conflicts(repo=header_repo, target='build/f.o',
                                      includes=['api.h'], admission=admission)
    assert report['conflicts'] == ['input']
    assert report['reference_body_supplied'] is False
    original = '#include "api.h"\nint f(void) { REFERENCE_BODY(); }\n'
    candidate = 'extern int input;\nint f(void) { return input; }\n'
    result = pi.replace_function(original, candidate, 'f', header_conflicts=report['conflicts'])
    assert 'extern int input;' not in result
    assert 'return (*(int *)&input);' in result


def test_equal_and_unknown_header_objects_do_not_change(header_repo):
    declarations = ['extern int same;', 'extern int unknown;']
    admission = pi.check_extern_declarations(repo=header_repo, target='build/f.o',
                                           includes=[], declarations=declarations)
    report = pi.header_object_conflicts(repo=header_repo, target='build/f.o',
                                      includes=['api.h'], admission=admission)
    assert report['conflicts'] == []


def test_header_signedness_conflict_and_typedef_equivalence_are_measured(header_repo):
    (header_repo / 'api.h').write_text('typedef int Word;\nextern Word same;\nextern unsigned char flag;\n')
    admission = pi.check_extern_declarations(repo=header_repo, target='build/f.o', includes=[],
                                           declarations=['extern int same;', 'extern signed char flag;'])
    report = pi.header_object_conflicts(repo=header_repo, target='build/f.o',
                                      includes=['api.h'], admission=admission)
    assert report['conflicts'] == ['flag']


def test_active_conditional_header_selection_uses_recipe(header_repo):
    (header_repo / 'api.h').write_text('#if 1\nextern int input[4];\n#else\nextern int input;\n#endif\n')
    admission = pi.check_extern_declarations(repo=header_repo, target='build/f.o', includes=[],
                                           declarations=['extern int input;'])
    assert pi.header_object_conflicts(repo=header_repo, target='build/f.o', includes=['api.h'],
                                    admission=admission)['conflicts'] == ['input']


def test_broken_header_probe_declines(header_repo):
    admission = pi.check_extern_declarations(repo=header_repo, target='build/f.o', includes=[],
                                           declarations=['extern int input;'])
    with pytest.raises(ValueError, match='header.*rejected'):
        pi.header_object_conflicts(repo=header_repo, target='build/f.o', includes=['missing.h'], admission=admission)


def test_shadowing_function_parameter_declines_typed_global_rewrite():
    body = 'int f(int input) { return input; }'
    assert pi._typed_uses(body, 'extern int input;') is None


def _prepare_fixture(header_repo, original, source):
    (header_repo / 'src').mkdir()
    (header_repo / 'src/f.c').write_text(original)
    (header_repo / 'snowboardkids.z64').write_bytes(b'ROM')
    candidate = header_repo / 'candidate.c'
    candidate.write_text(source)
    db = header_repo / 'trial.sqlite'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE functions(name,addr,tu_id); CREATE TABLE tus(id,name); '
                           'CREATE TABLE attempts(id,func_addr,source_code);')
        conn.execute("INSERT INTO functions VALUES ('f',1,1)")
        conn.execute("INSERT INTO tus VALUES (1,'build/src/f.o')")
        conn.execute('INSERT INTO attempts VALUES (1,1,?)', (candidate.read_text(),))
    entry = {'function': 'f', 'source': str(candidate), 'attempt_id': 1,
             'verification': {'exact': True, 'candidate_source_sha256': pi.sha(candidate.read_bytes())}}
    return pi.prepare(repo=header_repo, db=db, entries=[entry], output_dir=header_repo / 'prepared')


def test_full_preparation_routes_destination_header_conflict(header_repo):
    path = _prepare_fixture(header_repo, '#include "api.h"\nint f(void) { return 0; }\n',
                            'extern int input;\nint f(void) { return input; }\n')
    manifest = json.loads(path.read_text())
    output = (path.parent / '000.c').read_text()
    assert 'extern int input;' not in output
    assert 'return (*(int *)&input);' in output
    assert manifest['lineage'][0]['header_object_conflicts']['conflicts'] == ['input']


def test_unavailable_header_measurement_preserves_existing_preparation(header_repo):
    path = _prepare_fixture(header_repo, '#define FLAG 1\n#include "api.h"\nint f(void) { return 0; }\n',
                            'extern int unknown;\nint f(void) { return unknown; }\n')
    output = (path.parent / '000.c').read_text()
    assert 'extern int unknown;' in output and 'return unknown;' in output
    report = json.loads(path.read_text())['lineage'][0]['header_object_conflicts']
    assert report['conflicts'] == [] and report['status'] == 'unavailable'
    assert 'source-local' in report['error']


def test_header_extraction_ignores_comments_and_declines_source_local_selection():
    assert pi.destination_header_includes('/*\n#include "secret.h"\n*/\n#include "api.h"\n') == ['api.h']
    with pytest.raises(ValueError, match='conditional'):
        pi.destination_header_includes('#if FLAG\n#include "api.h"\n#endif\n')
    with pytest.raises(ValueError, match='source-local'):
        pi.destination_header_includes('#define FLAG 1\n#include "api.h"\n')


def test_data_initializer_includes_are_not_probed_as_headers():
    original = '#include "api.h"\nint table[] = {\n#include "values.inc.c"\n};\n'
    assert pi.destination_header_includes(original) == ['api.h']
