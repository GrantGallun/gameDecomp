from solver import principle_variants


def _by_label(variants):
    return {variant.label: variant.source for variant in variants}


def test_body_locator_advances_past_prototype_before_definition():
    source = "int f(int value);\nint f(int value) { return value; }\n"

    start, end = principle_variants._body_span(source, "f")

    assert source[start:end].strip() == "return value;"


def test_isolated_register_family_splits_and_directizes_pointer_web():
    source = """\
typedef unsigned char u8;
extern u8 table[256];
typedef struct State { u8 index; } State;
u8 next(State *arg0) {
    u8 *p = &arg0->index;
    u8 idx = *p; idx++; *p = idx;
    return table[*p];
}
"""

    variants = _by_label(
        principle_variants.isolated_register_web(source, "next"))

    assert "split-all-initializers" in variants
    assert "u8 *p;" in variants["split-all-initializers"]
    assert "u8 idx;" in variants["split-all-initializers"]
    assert "p = &arg0->index;" in variants["split-all-initializers"]
    assert "direct-value-web" in variants
    assert "u8 idx = arg0->index;" in variants["direct-value-web"]
    assert "arg0->index = idx;" in variants["direct-value-web"]
    assert "table[arg0->index]" in variants["direct-value-web"]
    assert "*p" not in variants["direct-value-web"]
    assert "direct-postincrement" in variants
    assert "arg0->index++;" in variants["direct-postincrement"]
    assert "fused-preincrement-lookup" in variants
    assert "return table[++arg0->index];" in variants[
        "fused-preincrement-lookup"]


def test_narrow_scope_ends_scalar_web_before_later_pointer_use():
    source = """\
typedef unsigned char u8;
extern u8 table[256];
typedef struct State { u8 index; } State;
u8 next(State *arg0) {
    u8 *p = &arg0->index; u8 idx = arg0->index; idx++; arg0->index = idx;
    return table[*p];
}
"""

    variants = _by_label(
        principle_variants.narrow_local_scopes(source, "next"))

    label = next(label for label in variants
                 if label.startswith("narrow-scope-idx-"))
    scoped = variants[label]
    assert "u8 *p = &arg0->index;\n    {" in scoped
    assert "        u8 idx = arg0->index;" in scoped
    assert "        idx++; arg0->index = idx;" in scoped
    assert scoped.index("    }") < scoped.index("return table[*p]")
    assert not any(label.startswith("narrow-scope-p-") for label in variants)


def test_narrow_scope_declines_address_escape_and_nested_control_flow():
    address_escape = """\
void f(void) {
    int keep;
    int value;
    keep = 0;
    observe(&value);
    keep++;
}
"""
    nested = """\
void f(void) {
    int keep;
    int value;
    keep = 0;
    if (keep) {
        value = 1;
    }
    keep++;
}
"""

    assert not any(row.label.startswith("narrow-scope-value-")
                   for row in principle_variants.narrow_local_scopes(
                       address_escape, "f"))
    assert not any(row.label.startswith("narrow-scope-value-")
                   for row in principle_variants.narrow_local_scopes(
                       nested, "f"))


def test_pointer_lifetime_can_inline_or_delay_stable_parameter_field():
    source = """\
typedef unsigned char u8;
extern u8 table[256];
typedef struct State { u8 index; } State;
u8 next(State *arg0) {
    u8 *p = &arg0->index; u8 idx = arg0->index; idx++; arg0->index = idx;
    return table[*p];
}
"""

    variants = _by_label(
        principle_variants.pointer_lifetime_variants(source, "next"))

    direct = variants["inline-pointer-lifetime-p"]
    assert "u8 *p" not in direct
    assert "return table[arg0->index];" in direct
    late = variants["late-pointer-lifetime-p"]
    assert late.index("arg0->index = idx") < late.index(
        "u8 *p = &arg0->index")
    assert "        return table[*p];" in late


def test_pointer_lifetime_declines_reassigned_base_or_non_deref_use():
    reassigned = """\
typedef struct State { int value; } State;
int f(State *arg0, State *other) {
    int *p = &arg0->value;
    int value = *p;
    arg0 = other;
    return *p + value;
}
"""
    non_deref = """\
typedef struct State { int value; } State;
int f(State *arg0) {
    int *p = &arg0->value;
    int value = *p;
    observe(p);
    return value;
}
"""

    assert principle_variants.pointer_lifetime_variants(
        reassigned, "f") == ()
    assert principle_variants.pointer_lifetime_variants(
        non_deref, "f") == ()


def test_fused_byte_update_lookup_builds_one_promoted_expression_web():
    source = """\
typedef unsigned char u8;
extern u8 table[256];
typedef struct State { u8 index; } State;
u8 next(State *arg0) {
    u8 *p = &arg0->index; u8 idx = arg0->index; idx++; arg0->index = idx;
    return table[*p & 0xFF];
}
"""

    rows = _by_label(
        principle_variants.fused_byte_update_lookup(source, "next"))

    assert "return table[++arg0->index];" in rows[
        "fuse-byte-update-lookup-idx"]
    assert "u8 *p" not in rows["fuse-byte-update-lookup-idx"]
    assert "u8 idx" not in rows["fuse-byte-update-lookup-idx"]
    combined = rows["combine-load-increment-idx"]
    assert "u8 idx = arg0->index + 1;" in combined
    assert "idx++;" not in combined
    assert "return table[*p & 0xFF];" in combined
    direct = rows["direct-byte-postincrement-idx"]
    assert "arg0->index++;" in direct
    assert "return table[arg0->index];" in direct
    assert "u8 *p" not in direct and "u8 idx" not in direct


def test_fused_byte_update_lookup_requires_unsigned_byte_destination():
    source = """\
typedef unsigned char u8;
extern u8 table[256];
typedef struct State { int index; } State;
u8 next(State *arg0) {
    u8 idx = arg0->index;
    idx++;
    arg0->index = idx;
    return table[arg0->index & 0xFF];
}
"""

    assert principle_variants.fused_byte_update_lookup(
        source, "next") == ()


def test_isolated_register_family_reorders_c89_declarations_and_globals():
    source = """\
typedef int s32;
extern s32 read_index;
extern s32 write_index;
s32 reserve(void) {
    s32 value;
    s32 other;

    value = read_index;
    if (value == write_index) return -1;
    read_index = value + 1;
    return value;
}
"""

    variants = _by_label(
        principle_variants.isolated_register_web(source, "reserve"))

    assert "split-declarations-swap-0-1" in variants
    swapped = variants["split-declarations-swap-0-1"]
    assert swapped.index("s32 other;") < swapped.index("s32 value;")
    assert "global-pointer-read_index" in variants
    pointer = variants["global-pointer-read_index"]
    assert "s32 *principle_read_index = &read_index;" in pointer
    assert "(*principle_read_index) = value + 1;" in pointer


def test_register_family_reaches_locals_after_fixed_padding_array_and_comment():
    """A stack-shaping array must not hide every scalar declaration lever."""
    source = """\
typedef unsigned char u8;
typedef signed int s32;
void compress(void) {
    volatile u8 framePad[0x10];

    /* register-transliterated semantic candidate */
    s32 v0 = 0; /* input index */
    s32 v1 = 1;
    s32 t0 = 0;

    consume(v0, v1, t0);
}
"""
    variants = _by_label(
        principle_variants.isolated_register_web(source, "compress",
                                                  max_variants=32))

    assert "split-all-initializers" in variants
    split = variants["split-all-initializers"]
    assert "volatile u8 framePad[0x10];" in split
    assert "register-transliterated semantic candidate" in split
    assert "s32 v0; /* input index */" in split
    assert "v0 = 0;" in split
    assert "split-declarations-swap-0-1" in variants


def test_variant_budget_and_deduplication_are_enforced():
    source = "int f(void) { int a; int b; int c; a = 1; return a; }"
    variants = principle_variants.isolated_register_web(
        source, "f", max_variants=3)

    assert len(variants) == 3
    assert len({variant.source for variant in variants}) == 3


def test_swaps_adjacent_disjoint_raw_offset_mutations():
    source = """\
typedef unsigned char u8;
typedef signed short s16;
typedef signed int s32;
#define STATE_TIMER(p) (*(s32 *)((u8 *)(p) + 0x7c))
#define UPDATE_TIMER(p) (*(s16 *)((u8 *)(p) + 0x304))
void update(void *player) {
    STATE_TIMER(player) += 0x16;
    UPDATE_TIMER(player)++;
    consume(player);
}
"""

    variants = principle_variants.independent_statement_order(
        source, "update")

    assert len(variants) == 1
    assert variants[0].label == "swap-independent-statements-7-8"
    assert variants[0].source.index("UPDATE_TIMER(player)++") < \
        variants[0].source.index("STATE_TIMER(player) += 0x16")


def test_does_not_swap_dependencies_overlaps_or_calls():
    dependent = """\
void f(void) {
    a += 1;
    b = a;
}
"""
    overlap = """\
typedef unsigned char u8;
typedef signed int s32;
#define WORD_A(p) (*(s32 *)((u8 *)(p) + 4))
#define WORD_B(p) (*(s32 *)((u8 *)(p) + 6))
void f(void *p) {
    WORD_A(p) += 1;
    WORD_B(p) += 1;
}
"""
    call = """\
void f(void) {
    a = observe();
    b += 1;
}
"""

    assert principle_variants.independent_statement_order(
        dependent, "f") == ()
    assert principle_variants.independent_statement_order(overlap, "f") == ()
    assert principle_variants.independent_statement_order(call, "f") == ()


def test_swaps_distinct_direct_fields_of_a_known_struct():
    source = """\
typedef struct {
    char pad00[0xc2];
    short unkC2;
    short unkC4;
} PlayerCommandState;
int Fendit(PlayerCommandState *arg0, unsigned char *arg1) {
    arg0->unkC2 = 0;
    arg0->unkC4 = (unsigned short) *arg1;
    return (int) (arg1 + 1);
}
"""

    variants = principle_variants.independent_statement_order(source, "Fendit")

    assert len(variants) == 1
    assert variants[0].source.index("arg0->unkC4") < \
        variants[0].source.index("arg0->unkC2")


def test_does_not_treat_distinct_union_members_as_disjoint():
    source = """\
typedef union {
    int left;
    int right;
} Pair;
void f(Pair *pair) {
    pair->left = 1;
    pair->right = 2;
}
"""

    assert principle_variants.independent_statement_order(source, "f") == ()


def test_materializes_late_pointer_rhs_before_disjoint_struct_write():
    source = """\
typedef unsigned short u16;
typedef struct { short left; short right; } Pair;
int f(Pair *pair, unsigned char *input) {
    pair->left = 0;
    pair->right = (u16) *input;
    return 1;
}
"""

    variants = principle_variants.materialize_aliased_rhs_before_write(
        source, "f")

    assert len(variants) == 1
    changed = variants[0].source
    assert changed.index("u16 exactnessTemp = (u16) *input;") < \
        changed.index("pair->left = 0;")
    assert "pair->right = exactnessTemp;" in changed


def test_materializes_uncast_pointer_rhs_using_pointee_type():
    source = """\
typedef unsigned char u8;
typedef struct { char left; char right; } Pair;
int f(Pair *pair, u8 *input) {
    pair->left = 0;
    pair->right = *input;
    return (int) (input + 1);
}
"""

    variants = principle_variants.materialize_aliased_rhs_before_write(
        source, "f")

    assert len(variants) == 1
    assert "u8 exactnessTemp = *input;" in variants[0].source


def test_materializes_pointer_return_around_existing_local():
    source = """\
typedef unsigned char u8;
typedef unsigned short u16;
typedef signed int s32;
s32 f(u8 *input) {
    u16 value = (u16) *input;
    output = value;
    return (s32) (input + 1);
}
"""

    variants = principle_variants.materialize_return_pointer_variants(
        source, "f")

    assert len(variants) == 2
    assert all("u8 *exactnessReturn = input + 1;" in row.source
               for row in variants)
    assert all("return (s32) exactnessReturn;" in row.source
               for row in variants)


def test_fuses_pointer_load_with_returned_increment():
    source = """\
typedef unsigned char u8;
typedef unsigned short u16;
typedef signed int s32;
s32 f(u8 *input) {
    u16 value = (u16) *input;
    output = value;
    return (s32) (input + 1);
}
"""

    variants = principle_variants.fuse_load_with_return_postincrement(
        source, "f")

    assert len(variants) == 1
    assert "u16 value = (u16) *input++;" in variants[0].source
    assert "return (s32) input;" in variants[0].source


def test_fuses_uncast_pointer_load_with_returned_increment():
    source = """\
typedef unsigned char u8;
typedef signed int s32;
s32 f(u8 *input) {
    u8 value = *input;
    output = value;
    return (s32) (input + 1);
}
"""

    variants = principle_variants.fuse_load_with_return_postincrement(
        source, "f")

    assert len(variants) == 1
    assert "u8 value = *input++;" in variants[0].source


def test_inlines_early_temp_store_before_intervening_disjoint_write():
    source = """\
typedef unsigned char u8;
typedef unsigned short u16;
typedef struct { short left; short right; } Pair;
int f(Pair *pair, u8 *input) {
    u16 value = (u16) *input++;
    pair->left = 0;
    pair->right = value;
    return (int) input;
}
"""

    variants = principle_variants.inline_early_temp_before_disjoint_write(
        source, "f")

    assert len(variants) == 1
    changed = variants[0].source
    assert "u16 value" not in changed
    assert changed.index("pair->right = ((u16) *input++);") < \
        changed.index("pair->left = 0;")


def test_fuses_direct_field_load_with_returned_pointer_increment():
    source = """\
typedef unsigned char u8;
typedef signed int s32;
typedef struct { char left; char right; } Pair;
s32 f(Pair *pair, u8 *input) {
    pair->right = (*input);
    pair->left = 0;
    return (s32) (input + 1);
}
"""

    variants = principle_variants.fuse_store_load_with_return_postincrement(
        source, "f")

    assert len(variants) == 1
    assert "pair->right = (*input++);" in variants[0].source
    assert "return (s32) input;" in variants[0].source


def test_reuses_same_typed_dead_parameter_for_local():
    source = """\
typedef signed short s16;
s16 fixedCosine(s16 arg0) {
    s16 angle;
    angle = (arg0 + 0x400) & 0xFFF;
    if (angle == 0x400) return 1;
    return table[angle];
}
"""

    variants = principle_variants.reuse_dead_parameter_for_local(
        source, "fixedCosine")

    assert len(variants) == 1
    changed = variants[0].source
    assert "s16 angle;" not in changed
    assert "arg0 = (arg0 + 0x400) & 0xFFF;" in changed
    assert "table[arg0]" in changed


def test_materializes_return_array_load_without_hoisting_across_branches():
    source = """\
typedef signed short s16;
extern s16 table[];
s16 f(s16 angle) {
    if (angle == 1) {
        return 2;
    }
    return (s16) ((s16) table[angle] >> 3);
}
"""

    variants = principle_variants.materialize_return_array_load(source, "f")

    assert len(variants) == 2
    changed = variants[0].source
    assert "s16 exactnessLoad;" in changed
    assert changed.index("if (angle == 1)") < \
        changed.index("exactnessLoad = (s16) table[angle];")
    assert changed.index("exactnessLoad = (s16) table[angle];") < \
        changed.index("return (s16) (exactnessLoad >> 3);")


def test_return_load_materialization_rejects_conditional_or_effectful_context():
    for expression in ("enabled ? table[i] : 0", "enabled && table[i]",
                       "table[i] + next()", "table[i] + table[j]"):
        source = ("extern short table[];\nint f(int i) {\n"
                  f"    return {expression};\n}}\n")
        assert principle_variants.materialize_return_array_load(source, "f") == ()


def test_return_load_materialization_keeps_unbraced_branch_as_one_statement():
    source = """\
extern short table[];
short f(int i) {
    if (i)
        return table[i];
    return 0;
}
"""
    variant, = principle_variants.materialize_return_array_load(source, "f")
    assert ("if (i)\n        {\n"
            "            exactnessLoad = table[i];\n"
            "            return exactnessLoad;\n        }\n"
            "    return 0;") in variant.source


def test_statement_order_variants_are_prioritized_in_register_family():
    source = """\
typedef unsigned char u8;
typedef signed short s16;
typedef signed int s32;
#define A(p) (*(s32 *)((u8 *)(p) + 0))
#define B(p) (*(s16 *)((u8 *)(p) + 8))
void f(void *p) {
    A(p) += 1;
    B(p)++;
}
"""
    variants = principle_variants.isolated_register_web(
        source, "f", max_variants=1)

    assert len(variants) == 1
    assert variants[0].label.startswith("swap-independent-statements")
