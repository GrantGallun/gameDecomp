"""Correctness tests for the symbolic tracer.

The test list is taken from an external critique which noted that every check so
far had used the two functions the analyzer was developed against -- development
-set debugging, not validation. Three of these correspond to bugs that were
shipped and only found later.

Each test states what it guards and, where applicable, what went wrong.
"""

import pytest

from solver import symtrace, tracefix


def T(asm: str) -> list[str]:
    return symtrace.trace(asm, max_events=50)


def calls(asm: str) -> list[str]:
    return [e for e in T(asm) if e.startswith("CALL")]


# --- delay slots -----------------------------------------------------------

def test_argument_set_in_the_delay_slot_is_included():
    """The instruction AFTER a jal executes BEFORE the call, and IDO routinely
    puts argument setup there. The first version emitted the CALL on sight of
    the jal and read arguments one instruction too early."""
    asm = "\n".join([
        "glabel f",
        "lw     $a1, 0x10($a0)",
        "jal    doThing",
        "lw     $a2, 0x14($a0)",      # delay slot: a REAL argument
        "jr     $ra",
    ])
    c = calls(asm)
    assert c, "no call traced"
    assert "param0->f14" in c[0], f"delay-slot argument missing: {c[0]}"


def test_delay_slot_does_not_leak_into_the_next_event():
    asm = "\n".join(["glabel f", "jal a", "nop", "lw $t0, 0x8($a0)",
                     "jr $ra"])
    assert len(calls(asm)) == 1


# --- register invalidation -------------------------------------------------

def test_caller_saved_registers_are_clobbered_by_a_call():
    """v0/v1, a0-a3 and t0-t9 do not survive a call. Without invalidating them
    a stale value leaks into every later event and reads as still-live."""
    asm = "\n".join([
        "glabel f",
        "lw     $a1, 0x10($a0)",
        "jal    first",
        "nop",
        "jal    second",
        "nop",
        "jr     $ra",
    ])
    c = calls(asm)
    assert len(c) == 2
    assert "param0->f10" not in c[1], \
        f"stale a1 survived the first call: {c[1]}"


def test_an_unset_register_is_not_printed_as_an_argument():
    """A register that was never set, or was clobbered, is not an argument.
    Printing "?a3" invents a parameter the call does not pass."""
    asm = "\n".join(["glabel f", "jal a", "nop", "jal b", "nop", "jr $ra"])
    for c in calls(asm):
        assert "?" not in c, f"unknown register printed as an argument: {c}"


# --- globals ---------------------------------------------------------------

def test_lui_alone_forms_a_global_address():
    """When a symbol's low half is zero, `lui` alone is the whole address. The
    first version stashed a pending %hi and never committed it, so
    addRenderCallback's first argument was silently reported as param0."""
    asm = "\n".join([
        "glabel f",
        "lui    $a0, %hi(gThing)",
        "jal    use",
        "nop",
        "jr     $ra",
    ])
    c = calls(asm)
    assert c and "&gThing" in c[0], f"global address lost: {c}"


def test_hi_lo_pair_also_forms_the_address():
    asm = "\n".join([
        "glabel f",
        "lui    $a0, %hi(gThing)",
        "addiu  $a0, $a0, %lo(gThing)",
        "jal    use",
        "nop",
        "jr     $ra",
    ])
    assert "&gThing" in calls(asm)[0]


# --- frame vs struct -------------------------------------------------------

def test_stack_stores_are_not_reported_as_struct_fields():
    """sp was being rewritten by the frame-setup addiu, so every frame store
    read as a field access on a struct."""
    asm = "\n".join([
        "glabel f",
        "addiu  $sp, $sp, -0x18",
        "sw     $ra, 0x14($sp)",
        "jr     $ra",
    ])
    ev = T(asm)
    assert any("stack[0x14]" in e for e in ev), ev


# --- prototype vs call site ------------------------------------------------

def test_typeword_separates_prototypes_from_call_sites():
    """This filter contained literal BACKSPACE bytes instead of \\b, so it
    matched nothing and every repair rewrote the extern declaration."""
    assert chr(8) not in tracefix.TYPEWORD.pattern
    assert tracefix.TYPEWORD.search("s32 a0, s32 a1")        # a prototype
    assert not tracefix.TYPEWORD.search("a0, a1, a2")        # a call
    assert not tracefix.TYPEWORD.search("p->field502, p->f1C")


def test_repair_leaves_the_prototype_alone():
    code = ('extern s32 doThing(s32 a0, s32 a1);\n'
            'void f(T *p) { doThing(p->field10, p->field14); }\n')
    asm = "\n".join(["glabel f", "lw $a0, 0x14($a0)", "jal doThing",
                     "lw $a1, 0x10($a0)", "jr $ra"])
    new, _log = tracefix.fix_calls(code, asm, ptr_name="p")
    assert "extern s32 doThing(s32 a0, s32 a1);" in new, \
        "the prototype was rewritten"


# --- arity -----------------------------------------------------------------

def test_void_call_is_not_given_an_argument():
    """`f(void)` takes ZERO arguments; treating "void" as one made the repair
    rewrite initGameSystems(void) into initGameSystems(arg0)."""
    code = "void f(T *p) { initThing(void); }\n"
    asm = "\n".join(["glabel f", "jal initThing", "nop", "jr $ra"])
    new, _ = tracefix.fix_calls(code, asm, ptr_name="p")
    assert "initThing(void)" in new, new


@pytest.mark.parametrize("nargs", [0, 1, 2, 3])
def test_argument_count_is_never_inflated(nargs):
    loads = [f"lw     $a{i}, 0x1{i}($a0)" for i in range(nargs)]
    asm = "\n".join(["glabel f"] + loads + ["jal g", "nop", "jr $ra"])
    c = calls(asm)
    if not c:
        return
    inner = c[0][c[0].index("(") + 1:c[0].rindex(")")] if ")" in c[0] else ""
    got = len([a for a in inner.split(",") if a.strip()])
    assert got <= max(nargs, 1), f"{nargs} args set, {got} reported: {c[0]}"


# --- honest limits ---------------------------------------------------------

def test_branches_are_reported_but_not_resolved():
    """Straight-line only. A value that differs between paths is NOT merged,
    and the trace must not pretend otherwise -- this documents the limit rather
    than asserting correctness the analyzer does not have."""
    asm = "\n".join([
        "glabel f",
        "beqz   $a0, .L1",
        "li     $a1, 1",
        "li     $a1, 2",
        ".L1:",
        "jal    g",
        "nop",
        "jr     $ra",
    ])
    ev = T(asm)
    assert any(e.startswith("branch") for e in ev), \
        "branches must at least be visible in the trace"
