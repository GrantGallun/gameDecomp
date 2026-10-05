"""Tests for the uopt allocation-trace parser and model check.

Fixture lines are copied from real `-zdbug:5`/`-zdbug:6` output of the patched uopt
(tools/ido-trace). Every check has a test that makes it FIRE, and every parsing trap
met on real output has one: the isvar listing that precedes its procedure's timing
line, listing positions that differ from live-range numbers, a negative adjsave with no
separating space, constant records, splits, and range notation in sets. The selection
rules have one test per rule, on the shape each was fitted to.
"""

from solver import uopt_trace as U

LEVEL5 = """\
{ 848|0}     58 isvar   M    3   -14vreg
{ 477|0}     91 isvar   P    3     0vreg
 * *    0. 20 SECONDS IN global coloring of first
>>>active<<<{477|0}           90           3
adjsave, hasstore: 1.42857149e-01  true
forbidden: [  1  4  5]
:::interfere with:::   58    2
- live bb (default) (  1) [   1,   3..   4]
>>>active<<<{848|0}          58           2
adjsave, hasstore: 2.00000000e+00  true
forbidden: [  1  3..   5]
:::interfere with:::   90    2
    7
>>>active<<<{1018|0}          96           2
const------>
          10           0
adjsave, hasstore: 5.00000000e-01 false
forbidden: [  1  3  4  5]
:::interfere with:::   90
% % % node   0 loopdepth            1notloopfirstbb
{ 900|0}      0 isvar   P   44     4vreg
 * *    0. 20 SECONDS IN global coloring of second
>>>active<<<{900|0}           0           4
adjsave, hasstore:-1.00000000e+00  true
forbidden: []
:::interfere with:::
"""

LEVEL6 = """\
   2:    2 assigned (constrained)   4
  90:   90 assigned (constrained)   3
   7:    7 assigned (unconstrained)   1
  58:   58 assigned (unconstrained)   2
  96:   96 assigned (unconstrained)   2
 * *    0. 28 SECONDS IN global coloring of first
  80:   80 not colored (-ve save)
live range  51:   51 split out  103
  51:  103 assigned (constrained)   7
 * *    0. 28 SECONDS IN global coloring of second
"""


def test_set_notation_with_ranges():
    assert U.parse_set("[   1,   3..   4,   6..   7]") == {1, 3, 4, 6, 7}
    assert U.parse_set("[  1  3..   5]") == {1, 3, 4, 5}
    assert U.parse_set("[]") == frozenset()


def test_level5_records_fire_on_every_field():
    first = U.parse_level5(LEVEL5)["first"]
    assert set(first) == {90, 58, 96}
    assert (first[90].node, first[90].color, first[90].adjsave, first[90].hasstore) == (477, 3, 0.142857149, True)
    assert first[58].forbidden == {1, 3, 4, 5}
    assert first[58].interferes == (90, 2, 7)          # wrapped continuation line
    assert first[96].constant and first[96].hasstore is False and first[96].adjsave == 0.5


def test_negative_adjsave_without_a_space_still_parses():
    assert U.parse_level5(LEVEL5)["second"][0].adjsave == -1.0


def test_nan_priority_with_stale_digits_parses_as_nan():
    # Real census line: `adjsave, hasstore:-.nan\x000000e-01  true`
    value = U.parse_level5(" * * 1 SECONDS IN global coloring of f\n>>>active<<<{1|0}  3  -1\n"
                           "adjsave, hasstore:-.nan\x000000e-01  true\n")["f"][3].adjsave
    assert value != value
    assert U.parse_real("Infinity") == float("inf") and U.parse_real("2.5e+00") == 2.5


def test_isvar_listing_belongs_to_the_procedure_whose_timing_line_follows():
    procs = U.parse_level5(LEVEL5)
    assert (procs["first"][90].kind, procs["first"][90].offset) == ("P", 0)
    assert (procs["first"][58].kind, procs["first"][58].offset) == ("M", -14)
    assert procs["first"][96].kind is None              # expression range, no isvar
    assert (procs["second"][0].kind, procs["second"][0].offset) == ("P", 4)


def test_isvar_joins_on_node_even_when_its_position_differs_from_the_range_number():
    # Position equals the range number for 98.4% of isvar lines on SBK1, not all:
    # `{477|0} 91 isvar` still names live range 90.
    assert U.parse_level5(LEVEL5)["first"][90].kind == "P"


def test_level6_attributes_decisions_to_the_following_timing_line():
    procs = U.parse_level6(LEVEL6)
    decisions, splits = procs["first"]
    assert [(d.piece, d.outcome, d.color) for d in decisions] == [
        (2, "constrained", 4), (90, "constrained", 3), (7, "unconstrained", 1),
        (58, "unconstrained", 2), (96, "unconstrained", 2)]
    decisions, splits = procs["second"]
    assert splits == [(51, 103)]
    assert [(d.lr, d.piece, d.outcome) for d in decisions] == [(80, 80, "not_colored"), (51, 103, "constrained")]
    assert decisions[0].reason == "-ve save"


def _proc(ranges, decisions):
    return U.Procedure("f", {r.lr: r for r in ranges}, decisions)


def _range(lr, color, forbidden, interferes=(), adjsave=1.0, kind=None, offset=None, blocks=(), default=()):
    return U.LiveRange(lr=lr, node=lr, color=color, adjsave=adjsave, forbidden=frozenset(forbidden),
                       interferes=tuple(interferes), kind=kind, offset=offset,
                       blocks=tuple(blocks), default_blocks=frozenset(default))


def _decide(lr, kind, color):
    return U.Decision(lr, lr, kind, color)


def test_consistent_procedure_passes():
    joined = U.join(LEVEL5, LEVEL6)["first"]
    joined.ranges[2] = _range(2, 4, [], (90, 58), adjsave=0.3, blocks=[(0, 1, 0, 4)])
    joined.ranges[7] = _range(7, 1, [], (58,))
    joined.ranges[90].blocks = ((0, 1, 0, 3),)
    report = U.check(joined)
    assert report["consistent"], report


def test_colour_mismatch_fires():
    report = U.check(_proc([_range(1, 2, [])], [_decide(1, "unconstrained", 1)]))
    assert report["colour_mismatch"] == [(1, 1, 2)]


def test_forbidden_not_at_time_fires():
    ranges = [_range(1, 1, [], (2,)), _range(2, 2, [], (1,))]
    report = U.check(_proc(ranges, [_decide(1, "constrained", 1), _decide(2, "unconstrained", 2)]))
    assert report["forbidden_not_at_time"] == [(2, [1])]


def test_selection_miss_fires_and_callee_saved_band_is_separate():
    ranges = [_range(1, 3, [1]), _range(2, 16, [3, 14, 15])]
    report = U.check(_proc(ranges, [_decide(1, "unconstrained", 3), _decide(2, "unconstrained", 16)]))
    assert report["selection_miss"] == [(1, 3, 2)]      # no preference: 2 was free; 16 is lowest in 14+


# Selection rules, each on the shape it was fitted to in the SBK1 census.

def test_block_rows_and_default_blocks_parse():
    text = (" * * 1 SECONDS IN global coloring of f\n>>>active<<<{573|0}  2  4\n"
            "- live bb -   0  1  0  4\nfirstisstr deadout needreglod needregsave   true false false false\n"
            "- live bb -  33  1  0  0\n- live bb (default) (  1) [   1,   3..   4]\n")
    record = U.parse_level5(text)["f"][2]
    assert record.blocks == ((0, 1, 0, 4), (33, 1, 0, 0))
    assert record.default_blocks == {1, 3, 4}
    assert record.preferences() == [4] and record.live_at_entry()


def test_preferred_colour_wins_over_a_free_v0():
    # tryStartPendingRdpTask-shaped: preference 3 blocked, next preference taken
    record = _range(1, 4, [3], blocks=[(2, 1, 0, 3), (3, 1, 0, 4)])
    assert U.select_colour(record, "int_caller") == 4


def test_blocked_preference_scans_upward_from_it():
    record = _range(1, 6, [3, 4, 5], blocks=[(1, 1, 0, 3)])
    assert U.select_colour(record, "int_caller") == 6


def test_preference_outside_the_band_is_ignored():
    record = _range(1, 1, [3], blocks=[(13, 2, 0, 20)])      # alAdpcmPull: callee pref, caller band
    assert U.select_colour(record, "int_caller") == 1


def test_parameter_live_at_entry_scans_from_a0_not_from_its_own_register():
    # _getVol: the a1 parameter, nothing forbidden, takes a0
    assert U.select_colour(_range(1, 3, [], kind="P", offset=4, blocks=[(0, 2, 1, 0)]), "int_caller") == 3
    # the fifth (stack) parameter with a0-a3 taken takes t0
    assert U.select_colour(_range(1, 7, [3, 4, 5, 6], kind="P", offset=16, default=[0]), "int_caller") == 7


def test_parameter_not_live_at_entry_takes_the_lowest_free():
    # addSchedulerClient range 15
    assert U.select_colour(_range(15, 2, [1, 3, 4], kind="P", offset=0, blocks=[(1, 2, 0, 0)]), "int_caller") == 2


def test_float_bands_and_float_parameters():
    assert U.band_of(24) == "float_caller" and U.band_of(23) == "int_callee" and U.band_of(30) == "float_callee"
    assert U.select_colour(_range(1, 24, []), "float_caller") == 24
    assert U.select_colour(_range(1, 27, [26], kind="P", offset=12, blocks=[(0, 1, 0, 0)]), "float_caller") == 27
    assert U.select_colour(_range(1, 30, [24]), "float_callee") == 30


def test_order_fires_on_rising_adjsave_late_constrained_and_falling_lr():
    ranges = [_range(1, 1, [], adjsave=1.0), _range(2, 2, [1], adjsave=5.0),
              _range(9, 3, [1, 2]), _range(4, 4, [1, 2, 3]), _range(5, 5, [1, 2, 3, 4], adjsave=9.0)]
    decisions = [_decide(1, "constrained", 1), _decide(2, "constrained", 2), _decide(9, "unconstrained", 3),
                 _decide(4, "unconstrained", 4), _decide(5, "constrained", 5)]
    order = U.check(_proc(ranges, decisions))["order"]
    assert [item[0] for item in order] == [2, 4, 5]


def test_nan_adjsave_is_listed_and_does_not_switch_off_the_order_check():
    ranges = [_range(1, 1, [], adjsave=3.0), _range(2, 2, [1], adjsave=float("nan")),
              _range(3, 3, [1, 2], adjsave=4.0)]
    decisions = [_decide(1, "constrained", 1), _decide(2, "constrained", 2), _decide(3, "constrained", 3)]
    report = U.check(_proc(ranges, decisions))
    assert report["nonfinite_adjsave"] == [2]
    assert [item[0] for item in report["order"]] == [3]   # 4.0 after 3.0 still fires past the NaN


def test_not_colored_decisions_are_not_scored():
    report = U.check(_proc([], [U.Decision(8, 8, "not_colored", reason="-ve save")]))
    assert report["decisions"] == 0 and report["consistent"]
