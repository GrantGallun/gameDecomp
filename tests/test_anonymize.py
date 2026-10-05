"""tools/anonymize.py: every searchable name goes, consistently, and nothing the compiler uses changes."""
from tools import anonymize as an

CONTEXT = """typedef int s32;
typedef struct MarioState { s32 health; s32 coins; } MarioState;
enum Level { LEVEL_CASTLE = 4 };
extern MarioState gMarioStates[1];
extern const char *gLevelName;
void play_sound(s32 id, s32 *pos);
"""
FN = """s32 mario_add_coins(MarioState *m, s32 n)
{
  s32 total;
  total = m->coins + n;
  if (total > LEVEL_CASTLE) { play_sound(total, &m->health); }
  gLevelName = "Castle";
  return total;
}
"""
PERTURBED = FN.replace("m->coins + n", "n + m->coins")


def test_every_declared_name_is_renamed_and_base_types_kept():
    mapping, (ctx, fn, pert) = an.anonymize(CONTEXT, [FN, PERTURBED])
    for name in ("MarioState", "health", "coins", "LEVEL_CASTLE", "Level", "gMarioStates", "gLevelName",
                 "play_sound", "mario_add_coins", "total"):
        assert name in mapping and name not in ctx + fn, name
    assert "s32" not in mapping and "s32" in fn
    assert mapping["mario_add_coins"].startswith("func_8") and mapping["gMarioStates"].startswith("D_8")
    assert "Castle" not in fn and '"??????"' in fn                 # string contents scrubbed, length kept


def test_one_map_keeps_original_and_perturbed_consistent():
    mapping, (_ctx, fn, pert) = an.anonymize(CONTEXT, [FN, PERTURBED])
    coins = mapping["coins"]
    assert f"n + m_" not in pert                                  # sanity: parameter renamed too
    assert pert == fn.replace(f"{mapping['m']}->{coins} + {mapping['n']}", f"{mapping['n']} + {mapping['m']}->{coins}")


def test_masked_listing_ignores_names_but_not_registers_or_numbers():
    a = ["lui    a0,%hi(gLevelName)", "jal    play_sound", "lw    t6,0x24(a0)"]
    b = ["lui    a0,%hi(D_80012340)", "jal    func_80023450", "lw    t6,0x24(a0)"]
    assert an.masked_listing(a) == an.masked_listing(b)
    assert an.masked_listing(["lw    t6,0x24(a0)"]) != an.masked_listing(["lw    t7,0x24(a0)"])
