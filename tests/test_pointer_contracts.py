"""Pointer contracts derived from callee instructions: fire on the fixed-point math shapes, decline escapes."""
import pytest

from solver import mips_differential as d, pointer_contracts as pc

# makeFixedRotationY (ROM-verified extracted assembly, 2026-09-14): a0 saved across two nested calls, 18 bytes written.
ROTATION_Y = """glabel makeFixedRotationY
    addiu      $sp, $sp, -0x20
    sw         $a1, 0x24($sp)
    or         $a2, $a0, $zero
    sw         $ra, 0x14($sp)
    lh         $a0, 0x26($sp)
    jal        fixedSine
     sw        $a2, 0x20($sp)
    lh         $a0, 0x26($sp)
    jal        fixedCosine
     sw        $v0, 0x1C($sp)
    lw         $v1, 0x1C($sp)
    lw         $a2, 0x20($sp)
    addiu      $t7, $zero, 0x1000
    negu       $t6, $v1
    sh         $v0, 0x0($a2)
    sh         $zero, 0x2($a2)
    sh         $t6, 0x4($a2)
    sh         $zero, 0x6($a2)
    sh         $t7, 0x8($a2)
    sh         $zero, 0xA($a2)
    sh         $v1, 0xC($a2)
    sh         $zero, 0xE($a2)
    sh         $v0, 0x10($a2)
    lw         $ra, 0x14($sp)
    addiu      $sp, $sp, 0x20
    jr         $ra
     nop
"""

# transformVec3iByFixedMatrix, first row only: reads through callee-saved copies, writes through a reloaded a2.
TRANSFORM = """glabel transformVec3iByFixedMatrix
    addiu      $sp, $sp, -0x40
    sw         $s1, 0x18($sp)
    or         $s1, $a1, $zero
    sw         $ra, 0x1C($sp)
    sw         $s0, 0x14($sp)
    sw         $a2, 0x48($sp)
    lw         $a3, 0x4($s1)
    lh         $a1, 0x6($a0)
    or         $s0, $a0, $zero
    sra        $a2, $a3, 31
    jal        __ll_mul
     sra       $a0, $a1, 31
    lh         $a1, 0x10($s0)
    lw         $a3, 0x8($s1)
    lw         $t1, 0x48($sp)
    sw         $v1, 0x0($t1)
    sw         $v1, 0x8($t1)
    lw         $ra, 0x1C($sp)
    lw         $s1, 0x18($sp)
    lw         $s0, 0x14($sp)
    jr         $ra
     addiu     $sp, $sp, 0x40
"""

ARITY = {"fixedSine": 1, "fixedCosine": 1, "__ll_mul": 4}.get


def contracts(assembly, arity):
    return {c.index: (c.kind, c.extent) for c in pc.analyze(d.Program.parse("f", assembly), arity, ARITY)}


def test_rotation_output_matrix_is_write_only_through_a_saved_copy():
    assert contracts(ROTATION_Y, 2) == {0: ("write", 18)}


def test_transform_reads_matrix_and_vector_and_writes_result():
    assert contracts(TRANSFORM, 3) == {0: ("read", 18), 1: ("read", 12), 2: ("write", 12)}


def test_without_nested_arity_a_pointer_left_in_a2_is_treated_as_escaping():
    with pytest.raises(pc.Declined, match="pointer passed to fixedSine"):
        pc.analyze(d.Program.parse("f", ROTATION_Y), 2)


@pytest.mark.parametrize("assembly, reason", [
    ("glabel f\n    jal g\n     nop\n    sh $zero, 0($a0)\n    jr $ra\n     nop\n", "no qualifying"),   # a0 clobbered by the call
    ("glabel f\n    or $a1, $a0, $zero\n    jal g\n     nop\n    jr $ra\n     nop\n", "pointer passed to g"),
    ("glabel f\n    lui $t0, 0x8000\n    sw $a0, 0x10($t0)\n    jr $ra\n     nop\n", "escapes into memory"),
    ("glabel f\n    addiu $a0, $a0, 4\n    sh $zero, 0($a0)\n    jr $ra\n     nop\n", "pointer arithmetic"),
    ("glabel f\n    beqz $a1, .L1\n     nop\n    sh $zero, 0($a0)\n.L1:\n    jr $ra\n     nop\n", "branch can skip"),
    ("glabel f\n    or $v0, $a0, $zero\n    sh $zero, 0($a0)\n    jr $ra\n     nop\n", "pointer returned"),
])
def test_declines_escapes_arithmetic_conditional_stores_and_returns(assembly, reason):
    arity = {"g": 2}.get
    with pytest.raises(pc.Declined, match=reason):
        pc.analyze(d.Program.parse("f", assembly), 2, arity)


def test_loops_decline_the_whole_callee():
    assembly = "glabel f\n.L0:\n    sh $zero, 0($a0)\n    bnez $a1, .L0\n     nop\n    jr $ra\n     nop\n"
    with pytest.raises(pc.Declined, match="backward branch"):
        pc.analyze(d.Program.parse("f", assembly), 2)


def test_unparseable_callee_stays_opaque_instead_of_raising(monkeypatch):
    # requestMusicSequenceBank branches into MusFxBankSetCurrent's `.L8007210C` tail (2026-09-14);
    # the raise escaped extend_environment and parked five callers' revalidate jobs.
    shared_tail = "glabel requestMusicSequenceBank\n    beqz $a0, .L8007210C\n     nop\n    jr $ra\n     nop\n"
    monkeypatch.setattr(pc, "verified_assembly", lambda repo, name: (shared_tail, "test"))

    class Environment:
        leaves, outputs = {}, {}
    rows = pc.extend_environment(None, Environment(), ["requestMusicSequenceBank"], {"requestMusicSequenceBank": 1})
    assert rows[0]["status"] == "opaque" and "L8007210C" in rows[0]["reason"]
    assert "requestMusicSequenceBank" not in Environment.outputs


class FakeMemory:
    def __init__(self, data):
        self.data = data

    def read(self, address, width):
        return self.data.get(address, 0)


class FakeRunner:
    def __init__(self, sp, data):
        self.reg = {"sp": sp}
        self.initial_reg = {"sp": d.INITIAL_SP}
        self.memory = FakeMemory(data)

    def get(self, name):
        return self.reg[name]


def test_stack_labels_ignore_frame_offset_but_keep_buffer_content():
    contract = pc.PointerContract((pc.ArgumentContract(0, tuple(range(4)), ()),), "test", "id")
    base_a, base_b = d.INITIAL_SP - 0x40, d.INITIAL_SP - 0x30
    content = {base_a + i: i + 1 for i in range(4)} | {base_b + i: i + 1 for i in range(4)}
    runner = FakeRunner(d.INITIAL_SP - 0x50, content)
    left = contract.labels(runner, (base_a, 7), ["stack+a", "0x7"])
    right = contract.labels(runner, (base_b, 7), ["stack+b", "0x7"])
    assert left == right and left[0].startswith("stack-read[4]:")
    runner.memory.data[base_b + 2] = 99
    assert contract.labels(runner, (base_b, 7), ["stack+b", "0x7"]) != left


def test_uncontracted_or_aliased_stack_arguments_stay_unsupported():
    contract = pc.PointerContract((pc.ArgumentContract(0, (), (0, 1)), pc.ArgumentContract(1, (0,), ())), "test", "id")
    runner = FakeRunner(d.INITIAL_SP - 0x50, {})
    with pytest.raises(d.UnsupportedInstruction, match="aliased"):
        contract.labels(runner, (d.INITIAL_SP - 0x20, d.INITIAL_SP - 0x1F), ["a", "b"])
    single = pc.PointerContract((pc.ArgumentContract(0, (), (0,)),), "test", "id")
    with pytest.raises(d.UnsupportedInstruction, match="uncontracted"):
        single.labels(runner, (d.INITIAL_SP - 0x20, d.INITIAL_SP - 0x10), ["a", "b"])
