"""Frontend fix-its: parse clang range diagnostics and wrap the diagnosed expression in the named cast.

Range ends are exclusive, as real clang prints them with -fdiagnostics-print-source-range-info
(fadeOutAllMusicSequences: `{20:18-20:31}` spans exactly `&gAudioThread`).
"""
from solver import frontend_fixits as ff

SOURCE = """void fadeOutAllMusicSequences(void) {
    osStopThread(&gAudioThread);
    x->ptr = 0x80001000;
    return;
}
"""

CLANG = """candidate.c:2:18:{2:18-2:31}: error: incompatible pointer types passing 'struct AudioThread **' to parameter of type 'struct AudioThread *'; remove & [-Werror,-Wincompatible-pointer-types]
candidate.c:3:14:{3:5-3:11}{3:14-3:24}: error: incompatible integer to pointer conversion assigning to 'void *' from 'int' [-Werror,-Wint-conversion]
candidate.c:9:1: note: something unrelated
"""


def test_diagnose_reads_target_types_and_expression_ranges():
    fixes = ff.diagnose(CLANG)
    assert [(f.line, f.col, f.end_col, f.cast) for f in fixes] == [
        (2, 18, 31, "struct AudioThread *"), (3, 14, 24, "void *")]


def test_apply_wraps_whole_expressions_as_casts():
    fixed = ff.apply(SOURCE, ff.diagnose(CLANG))
    # The motivating rejection (fadeOutAllMusicSequences, object-exact, 2026-09-14).
    assert "osStopThread(((struct AudioThread *) (&gAudioThread)));" in fixed
    assert "x->ptr = ((void *) (0x80001000));" in fixed


def test_incomplete_pointer_arithmetic_uses_byte_pointer_and_unknown_errors_are_ignored():
    clang = ("candidate.c:2:10:{2:10-2:12}: error: arithmetic on a pointer to an incomplete type 'struct Foo'\n"
             "candidate.c:3:1:{3:1-3:3}: error: call to undeclared function 'bar' [-Wimplicit-function-declaration]\n")
    fixes = ff.diagnose(clang)
    assert len(fixes) == 1 and fixes[0].cast == "u8 *"


def test_nested_argument_and_return_casts_preserve_both_complete_expressions():
    source = 'void f(void) {\n    x = convert(&state);\n}\n'
    diagnostics = (
        "candidate.c:2:17:{2:17-2:23}: error: incompatible pointer types passing 'int *' to parameter of type 'Transform3D *'\n"
        "candidate.c:2:9:{2:5-2:6}{2:9-2:24}: error: incompatible pointer to integer conversion assigning to 'long' from 'Mtx *'\n"
    )
    fixed = ff.apply(source, ff.diagnose(diagnostics))
    assert fixed == 'void f(void) {\n    x = ((long) (convert(((Transform3D *) (&state)))));\n}\n'


def test_crossing_expression_ranges_are_declined_without_corrupting_source():
    source = 'a + b + c'
    fixes = [ff.Fix(1, 1, 1, 6, 'long', ''), ff.Fix(1, 5, 1, 10, 'int', '')]
    assert ff.apply(source, fixes) == source


def test_adjacent_and_duplicate_ranges_remain_independent():
    source = 'a+b'
    fixes = [ff.Fix(1, 1, 1, 2, 'int', ''), ff.Fix(1, 3, 1, 4, 'long', ''),
             ff.Fix(1, 1, 1, 2, 'int', '')]
    assert ff.apply(source, fixes) == '((int) (a))+((long) (b))'


def test_partial_cast_is_available_only_to_an_opted_in_search(tmp_path, monkeypatch):
    source = 'void f(int *slot, void *value) {\n    *slot = value;\n    missing();\n}\n'
    fixed = source.replace('*slot = value;', '*slot = ((int) (value));')
    conversion = ("candidate.c:2:13:{2:5-2:10}{2:13-2:18}: error: incompatible pointer to "
                  "integer conversion assigning to 'int' from 'void *'\n")
    residual = "candidate.c:3:5: error: call to undeclared function 'missing'\n"
    monkeypatch.setattr(ff, 'run_frontend', lambda repo, text, command, workdir:
                        (1, residual if text == fixed else conversion + residual))
    assert ff.propose(tmp_path, source, [], tmp_path)[0] is None
    candidate, trace = ff.propose(tmp_path, source, [], tmp_path, allow_partial=True)
    assert candidate == fixed
    assert trace[-1]['errors'] == 1


def test_partial_cast_does_not_treat_a_failed_frontend_as_zero_errors(tmp_path, monkeypatch):
    source = 'int f(void *value) {\n    return value;\n}\n'
    diagnostic = ("candidate.c:2:12:{2:12-2:17}: error: incompatible pointer to integer "
                  "conversion returning 'void *' from a function with result type 'int'\n")
    monkeypatch.setattr(ff, 'run_frontend', lambda repo, text, command, workdir:
                        (1, diagnostic if text == source else 'clang: error: command failed\n'))
    assert ff.propose(tmp_path, source, [], tmp_path, allow_partial=True)[0] is None


def test_partial_cast_requires_a_complete_original_observation(tmp_path, monkeypatch):
    source = 'int f(void *value) {\n    return value;\n}\n'
    conversion = ("candidate.c:2:12:{2:12-2:17}: error: incompatible pointer to integer "
                  "conversion returning 'void *' from a function with result type 'int'\n")
    initial = conversion + "include/bad.h:1:1: error: unknown type name 'Broken'\n"
    later = ''.join(f"candidate.c:{i}:1: error: undeclared identifier 'x{i}'\n" for i in range(1, 6))
    monkeypatch.setattr(ff, 'run_frontend', lambda repo, text, command, workdir:
                        (1, initial if text == source else later))
    assert ff.propose(tmp_path, source, [], tmp_path, allow_partial=True)[0] is None
