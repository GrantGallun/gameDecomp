from solver import mips_differential as differential
from solver import semantic_gradient


def _write(address, width, value, provenance):
    return differential.WriteEvent(
        ordinal=0, instruction=18, address=address, width=width, value=value,
        value_provenance=provenance)


def test_write_pair_yields_output_and_input_access_gradients():
    target = _write(
        "player+0x2f6", 2, 0xdd06,
        "i18 addu(i5 lh player+0x2f6/2 -> 0xffffdc86, constant 0x80)")
    candidate = _write(
        "player+0x2ee", 2, 0x7370,
        "i18 addu(i5 lh player+0x2f0/2 -> 0x000073f0, constant -0x80)")

    constraints = semantic_gradient.constraints_from_write_pair(
        target, candidate)

    assert [(row.role, row.candidate_offset, row.target_offset,
             row.target_type) for row in constraints] == [
        ("write", 0x2EE, 0x2F6, "s16"),
        ("read", 0x2F0, 0x2F6, "s16"),
    ]


def test_struct_sites_use_real_abi_layout_not_member_names():
    source = """\
typedef struct {
    char pad[0x2ee];
    s16 unk2EE;
    s16 unk2F6;
} Player;
void f(Player *p) {
    s16 x = p->unk2F6;
    p->unk2EE = x;
}
"""

    sites = semantic_gradient.access_sites(source)

    assert [(row.text, row.offset, row.role) for row in sites] == [
        ("p->unk2F6", 0x2F0, "read"),
        ("p->unk2EE", 0x2EE, "write"),
    ]


def test_joint_variant_bridges_lean_read_and_first_write():
    source = """\
typedef struct {
    char pad[0x2ee];
    s16 unk2EE;
    s16 unk2F6;
} Player;
void f(Player *player) {
    s16 t4 = player->unk2F6;
    player->unk2EE = t4 + 1;
    player->unk2EE = 7;
}
"""
    constraints = (
        semantic_gradient.AccessConstraint(
            "write", "player", 0x2EE, 0x2F6, 2, "s16", 7),
        semantic_gradient.AccessConstraint(
            "read", "player", 0x2F0, 0x2F6, 2, "s16", 7),
    )

    variants = semantic_gradient.rebinding_variants(source, constraints)

    wanted = [row for row in variants
              if "line 8 write" in row.label and "line 7 read" in row.label]
    assert len(wanted) == 1
    assert "s16 t4 = (*(s16 *)((char *)(player) + 0x2f6));" in wanted[0].source
    assert "(*(s16 *)((char *)(player) + 0x2f6)) = t4 + 1;" in wanted[0].source
    assert "player->unk2EE = 7;" in wanted[0].source


def test_macro_sites_distinguish_assignment_target_from_rhs():
    source = """\
#define VX(p) (*(s32 *)((char *)(p) + 0x1c))
void f(void *player) {
    VX(player) += VX(player);
}
"""

    sites = semantic_gradient.access_sites(source)

    assert [(row.offset, row.role, row.form) for row in sites] == [
        (0x1C, "readwrite", "offset-macro"),
        (0x1C, "read", "offset-macro"),
    ]


def test_phase_load_delta_enumerates_misbound_load_targets():
    target = """\
sw zero,0(a0)
lh t0,6(a0)
lh t1,8(a0)
addu t2,t0,t1
sh t2,4(a0)
jr ra
nop
"""
    candidate = """\
sw zero,0(a0)
lh t1,2(a0)
addiu t2,t1,1
sh t2,4(a0)
jr ra
nop
"""
    result = differential.run_suite(
        target, candidate, (differential.TestCase("case", 5),))[0]

    target_only, candidate_only = semantic_gradient.phase_load_delta(result)
    assert [(row.offset, row.width) for row in target_only] == [(6, 2), (8, 2)]
    assert [(row.offset, row.width) for row in candidate_only] == [(2, 2)]
    constraints = semantic_gradient.constraints_from_result(result)
    mappings = {(row.candidate_offset, row.target_offset)
                for row in constraints if row.role == "read"}
    assert mappings == {(2, 6), (2, 8)}


def test_dependency_slice_finds_output_parameter_and_stale_local():
    source = """\
#define FIELD(p) (*(s16 *)((char *)(p) + 0x2ee))
void f(void *player, s32 a1) {
    s32 t4;
    s32 prod;
    s32 t6;
    t4 = FIELD(player);
    prod = t4 * 3;
    t6 = prod >> 2;
    FIELD(player) = a1 + t6;
}
"""

    sites = semantic_gradient.dependency_sites(source, 0x2EE, 2)

    assert ("t4", 7) in {(row.name, row.line) for row in sites}
    assert ("a1", 9) in {(row.name, row.line) for row in sites}
    missing = (semantic_gradient.MissingReadConstraint(
        "player", 0x2F6, 2, "s16", 7),)
    variants = semantic_gradient.dependency_injection_variants(
        source, missing, 0x2EE, 2)
    assert any("line 7 value `t4`" in row.label for row in variants)
    assert any("line 9 value `a1`" in row.label for row in variants)


def test_dynamic_value_dag_aligns_entry_register_to_target_memory_leaf():
    target = """\
lh t0,6(a0)
addu t1,t0,t0
sh t1,4(a0)
jr ra
nop
"""
    candidate = """\
addu t1,a1,a1
sh t1,4(a0)
jr ra
nop
"""
    result = differential.run_suite(
        target, candidate, (differential.TestCase("case", 7),))[0]

    mismatches = semantic_gradient.value_mismatches_from_result(result)

    assert [(row.kind, row.path, row.target.offset, row.candidate.token)
            for row in mismatches] == [
        ("entry-to-memory", (0,), 6, "a1"),
        ("entry-to-memory", (1,), 6, "a1"),
    ]


def test_dynamic_value_dag_distinguishes_fresh_and_stale_same_offset_loads():
    target = """\
lh t0,6(a0)
addiu t1,t0,1
sh t1,6(a0)
lh t2,6(a0)
addu t3,t2,t2
sh t3,4(a0)
jr ra
nop
"""
    candidate = """\
lh t0,6(a0)
addiu t1,t0,1
sh t1,6(a0)
addu t3,t0,t0
sh t3,4(a0)
jr ra
nop
"""
    result = differential.run_suite(
        target, candidate, (differential.TestCase("case", 11),))[0]

    mismatches = semantic_gradient.value_mismatches_from_result(result)

    assert len(mismatches) == 2
    assert all(row.kind == "stale-memory-version" for row in mismatches)
    assert all(row.target.write_version == 0 for row in mismatches)
    assert all(row.candidate.write_version == -1 for row in mismatches)


def test_dag_guided_variants_can_replace_two_entry_uses_jointly():
    source = """\
#define OUT(p) (*(s16 *)((char *)(p) + 0x2ee))
void f(void *player, s32 a1) {
    OUT(player) = a1 + a1;
}
"""
    target = semantic_gradient.ValueNode(
        "memory", "lh", region="player", offset=0x2EE, width=2,
        write_version=-1)
    candidate = semantic_gradient.ValueNode("entry", "entry", "a1")
    mismatches = (
        semantic_gradient.ValueMismatch(
            "entry-to-memory", (0,), target, candidate, 7),
        semantic_gradient.ValueMismatch(
            "entry-to-memory", (1,), target, candidate, 7),
    )

    variants = semantic_gradient.dag_guided_variants(
        source, mismatches, 0x2EE, 2)

    assert any(row.source.count("0x2ee" ) == 3 for row in variants)


def test_dag_guided_variants_replace_stale_local_use_with_fresh_load():
    source = """\
#define FIELD(p) (*(s16 *)((char *)(p) + 0x2f6))
#define OUT(p) (*(s16 *)((char *)(p) + 0x2ee))
void f(void *player) {
    s32 t4;
    t4 = FIELD(player);
    FIELD(player) = t4 + 1;
    OUT(player) = t4 * 3;
}
"""
    target = semantic_gradient.ValueNode(
        "memory", "lh", region="player", offset=0x2F6, width=2,
        write_version=0)
    candidate = semantic_gradient.ValueNode(
        "memory", "lh", region="player", offset=0x2F6, width=2,
        write_version=-1)
    mismatches = (semantic_gradient.ValueMismatch(
        "stale-memory-version", (0,), target, candidate, 7),)

    variants = semantic_gradient.dag_guided_variants(
        source, mismatches, 0x2EE, 2)

    assert any(
        "OUT(player) = (*(s16 *)((char *)(player) + 0x2f6)) * 3;"
        in row.source for row in variants)


def test_operation_gradient_renders_path_trees_and_source_cone():
    target = """
        lw t0,0(a0)
        addu t1,t0,a1
        sw t1,4(a0)
        jr ra
        nop
    """
    candidate = target.replace("addu t1,t0,a1", "subu t1,t0,a1")
    source = """typedef int s32;
#define INPUT(p) (*(s32 *)((char *)(p) + 0))
#define OUTPUT(p) (*(s32 *)((char *)(p) + 4))
void f(void *player, s32 arg) {
    s32 value;
    value = INPUT(player) + arg;
    OUTPUT(player) = value;
}
"""
    result = differential.run_suite(
        target, candidate,
        (differential.TestCase(
            "case", 4, player_writes=((0, 4, 10),),
            entry_registers=(("a1", 3),)),))[0]

    rendered = semantic_gradient.render_operation_gradient([result], source)

    assert "DYNAMIC OPERATION-DAG GRADIENT" in rendered
    assert "kind=operation-shape; support=1/1" in rendered
    assert "TARGET EXECUTED VALUE TREE" in rendered
    assert "CANDIDATE EXECUTED VALUE TREE" in rendered
    assert "value = INPUT(player) + arg;" in rendered
