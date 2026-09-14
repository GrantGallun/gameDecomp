"""Tests for verified callee signatures and their propagation to callers."""

from solver import protostore, rewrites

MATCHED = ('#include "common.h"\n'
           '\n'
           'void *getRelocatableHeapBlockBase(s32 handle) {\n'
           '    return gAliases[handle].start;\n'
           '}\n')


def setup_function(_fn):
    protostore.clear()


def teardown_function(_fn):
    protostore.clear()


def test_parses_a_verified_definition():
    sig = protostore.parse_definition(MATCHED, "getRelocatableHeapBlockBase")
    assert sig is not None
    assert sig["ret"].replace(" ", "") == "void*"
    assert sig["params"] == "s32 handle"


def test_a_call_is_not_mistaken_for_a_definition():
    code = 'void f(void) {\n    g(1);\n    someCall(a, b);\n}\n'
    assert protostore.parse_definition(code, "someCall") is None


def test_store_is_inert_until_loaded():
    caller = 'extern void getRelocatableHeapBlockBase();\n'
    assert rewrites.prototype_rewrites(caller, "") == []


def test_replaces_a_wrong_declaration_with_the_verified_one():
    protostore.record("getRelocatableHeapBlockBase",
                      protostore.parse_definition(
                          MATCHED, "getRelocatableHeapBlockBase"))
    caller = ('#include "common.h"\n'
              'extern void getRelocatableHeapBlockBase();\n'
              'void c(void) { getRelocatableHeapBlockBase(3); }\n')
    rws = rewrites.prototype_rewrites(caller, "")
    assert rws
    out = rws[0](caller)
    assert "void *getRelocatableHeapBlockBase(s32 handle);" in out
    assert "extern void getRelocatableHeapBlockBase();" not in out


def test_inserts_a_prototype_when_the_caller_declares_none():
    protostore.record("getRelocatableHeapBlockBase",
                      protostore.parse_definition(
                          MATCHED, "getRelocatableHeapBlockBase"))
    caller = ('#include "common.h"\n'
              'void c(void) { getRelocatableHeapBlockBase(3); }\n')
    rws = rewrites.prototype_rewrites(caller, "")
    assert rws
    assert "void *getRelocatableHeapBlockBase(s32 handle);" in rws[0](caller)


def test_declines_when_the_declaration_already_matches():
    protostore.record("getRelocatableHeapBlockBase",
                      protostore.parse_definition(
                          MATCHED, "getRelocatableHeapBlockBase"))
    caller = ('#include "common.h"\n'
              'void *getRelocatableHeapBlockBase(s32 handle);\n'
              'void c(void) { getRelocatableHeapBlockBase(3); }\n')
    assert rewrites.prototype_rewrites(caller, "") == []


def test_never_rewrites_the_function_the_file_defines():
    """Repairing a function's own signature from its own match is circular."""
    protostore.record("getRelocatableHeapBlockBase",
                      protostore.parse_definition(
                          MATCHED, "getRelocatableHeapBlockBase"))
    assert rewrites.prototype_rewrites(MATCHED, "") == []


def test_return_type_and_name_need_a_separator():
    """Without one, the engine splits a single identifier: `f32 __MusIntPower
    Of2(...)` parsed as ret='f32 __MusIntPowerO', name='f2', and 82 of 83
    verified sources silently yielded nothing."""
    src = "f32 __MusIntPowerOf2(f32 arg0) {\n    return arg0;\n}\n"
    sig = protostore.parse_definition(src, "__MusIntPowerOf2")
    assert sig is not None
    assert sig["ret"] == "f32"
    assert sig["prototype"] == "f32 __MusIntPowerOf2(f32 arg0);"


def test_brace_on_the_following_line_still_parses():
    src = "void requestRumbleMotorStart(u16 idx)\n{\n    return;\n}\n"
    sig = protostore.parse_definition(src, "requestRumbleMotorStart")
    assert sig and sig["prototype"] == "void requestRumbleMotorStart(u16 idx);"


def test_pointer_return_keeps_its_spelling():
    src = "void *getBase(s32 h) {\n    return 0;\n}\n"
    sig = protostore.parse_definition(src, "getBase")
    assert sig["prototype"] == "void *getBase(s32 h);"
