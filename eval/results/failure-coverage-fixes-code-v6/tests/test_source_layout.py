from solver import source_layout


MODE53_PARTIAL = r"""
typedef struct {
    s32 x;
    s32 y;
    s32 z;
} Vector3;

typedef struct {
    /* 0x00 */ char pad0[0x20];
    /* 0x20 */ Vector3 position; /* 0x20,0x24,0x28 */
    /* 0x28 */ Vector3 velocity; /* 0x28,0x2C,0x30 */
    /* 0x30 */ char pad1[0x10];
    /* 0x40 */ Vector3 unknown1; /* 0x40,0x44,0x48 */
    /* 0x48 */ char pad2[0x14];
    /* 0x5C */ char pad3[0x04];
    /* 0x60 */ s32 unk74;       /* 0x74 */
    /* 0x64 */ s16 unk6E;       /* 0x6E */
    /* 0x66 */ s16 unk304;      /* 0x304 */
    /* 0x68 */ s32 stateTimer;  /* 0x7C */
    /* 0x6C */ s16 updateState; /* 0x302 */
} RacePlayer;
"""


def _fields(source):
    return {field.name: field for layout in source_layout.layouts(source)
            for field in layout.fields}


def test_exposes_mode53_partial_struct_cascade():
    fields = _fields(MODE53_PARTIAL)

    assert fields["position"].offset == 0x20
    assert fields["velocity"].offset == 0x2C
    assert fields["unknown1"].offset == 0x48
    assert fields["unk74"].offset == 0x6C
    assert fields["updateState"].offset == 0x78
    assert {claim.offset for claim in fields["unk74"].claims} == {
        0x60, 0x74}

    warning = source_layout.prompt_warning(MODE53_PARTIAL)
    assert "RacePlayer.velocity" in warning
    assert "laid out at `0x2c`" in warning
    assert "RacePlayer.unk74" in warning
    assert "entire partial struct as untrusted" in warning


def test_detects_high_offset_member_packed_after_short_padding():
    source = r"""
typedef struct {
    char pad[0x2ee];
    s16 unk2EE;
    s16 unk2F6;
} Player;
"""
    fields = _fields(source)

    assert fields["unk2EE"].offset == 0x2EE
    assert fields["unk2F6"].offset == 0x2F0
    assert not fields["unk2EE"].mismatches
    assert fields["unk2F6"].mismatches[0].offset == 0x2F6


def test_accepts_an_internally_consistent_layout():
    source = r"""
typedef struct {
    /* 0x00 */ s16 unk0;
    char pad02[6];
    /* 0x08 */ s32 unk8;
} State;
"""
    assert source_layout.mismatches(source) == ()
    assert source_layout.prompt_warning(source) == ""


def test_declines_unknown_nested_types_instead_of_guessing():
    source = "typedef struct { Mystery value; s32 unk4; } State;"
    assert source_layout.layouts(source) == ()


def test_counts_attached_pointer_and_function_pointer_declarators():
    source = """
struct CallbackTask {
    CallbackTask *prev;
    CallbackTask *next;
    void (*callback)(void *);
    u16 type;
    u16 priority;
    u8 pad[6];
    u8 isActive; /* 0x16 */
};
"""
    fields = _fields(source)
    assert fields["callback"].offset == 8
    assert fields["isActive"].offset == 0x16
    assert not fields["isActive"].mismatches
    assert source_layout.layouts(source)[0].size == 24


def test_unknown_field_cannot_be_silently_dropped():
    for declaration in ("unsigned flag:1;", "SOME_MACRO(field)", "int x; int y;",
                        "int x; unsigned flag:1;"):
        source = "struct State {\n" + declaration + "\n u16 tail;\n};"
        assert source_layout.layouts(source) == ()


def test_audits_named_structs_used_by_mode37_style_drafts():
    source = r"""
struct RacePlayer {
    char pad00[0x302];
    s16 updateState; /* 0x302 */
    s16 updateTimer; /* 0x304 */
    s32 unk7C;       /* 0x7C */
    s16 unk254;      /* 0x254 */
};
"""
    fields = _fields(source)

    assert fields["updateTimer"].offset == 0x304
    assert fields["unk7C"].offset == 0x308
    assert fields["unk254"].offset == 0x30C
    warning = source_layout.prompt_warning(source)
    assert "RacePlayer.unk7C" in warning
    assert "RacePlayer.unk254" in warning
