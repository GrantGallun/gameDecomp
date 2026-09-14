"""Tests for the uopt allocator model.

The quoted parts of the specification are pinned exactly, because a silent
drift in nocs or the colour pool would invalidate every ranking built on them.
"""

from solver import uopt


def test_colour_pool_matches_the_measured_order():
    """c1=v0 c2=v1 c3=a0 c4=a1 c5=a2 c6=a3 c7=t0 ... then s0-s8."""
    assert uopt.COLOR_POOL[:7] == ["v0", "v1", "a0", "a1", "a2", "a3", "t0"]
    assert uopt.COLOR_POOL[11] == "t5"
    assert uopt.COLOR_POOL[12] == "s0"
    assert uopt.COLOR_INDEX["v0"] < uopt.COLOR_INDEX["t0"] < uopt.COLOR_INDEX["s0"]


def test_nocs_formula_is_exact():
    """nocs = ((n - 2) >> 2) + 2, quoted from the instrumented measurement."""
    cases = {2: 2, 3: 2, 4: 2, 5: 2, 6: 3, 10: 4}
    for n, want in cases.items():
        w = uopt.Web(number=1, register="t0", occurrences=n)
        assert w.nocs == want, f"n={n}"


def test_save_is_non_monotone_in_occurrences():
    """Adding one read can LOWER priority -- why dead filler is dangerous.

    nocs steps at n = 6, so a web read five times at weight 1 scores 5/2 = 2.5
    while the same web read a sixth time scores 6/3 = 2.0. The extra
    occurrence raises totalsave and still loses.
    """
    five = uopt.Web(number=1, register="t0", occurrences=5, weight=5.0)
    six = uopt.Web(number=2, register="t1", occurrences=6, weight=6.0)
    assert five.nocs == 2 and six.nocs == 3
    assert six.weight > five.weight
    assert six.save < five.save


def test_allocation_order_breaks_ties_on_construction():
    a = uopt.Web(number=7, register="t1", occurrences=2, weight=2.0)
    b = uopt.Web(number=3, register="t0", occurrences=2, weight=2.0)
    assert [w.number for w in uopt.allocation_order([a, b])] == [3, 7]


def test_webs_split_at_redefinition():
    asm = "f:\n\tli\t$8, 1\n\taddu\t$9, $8, $8\n\tli\t$8, 2\n\taddu\t$9, $8, $8\n"
    ws = uopt.webs(asm)
    t0 = [w for w in ws if w.register == "t0"]
    assert len(t0) == 2, "a redefinition must close the previous web"


def test_non_pool_registers_are_ignored():
    asm = "f:\n\tsw\t$31, 20($sp)\n\tmove\t$8, $4\n"
    regs = {w.register for w in uopt.webs(asm)}
    assert "sp" not in regs and "ra" not in regs
    assert "t0" in regs


def test_argument_register_before_a_call_is_precolored():
    """a3 holding a call argument is the ABI, not an allocation decision.

    Counting it as one inverted the ranking completely on
    isRacePlayerRespawnSurfaceValid: 0% concordant across 14 pairs.
    """
    asm = "f:\n\tli\t$7, 3\n\tjal\tg\n\tnop\n"
    ws = uopt.webs(asm)
    uopt.mark_precolored(ws, asm)
    a3 = [w for w in ws if w.register == "a3"]
    assert a3 and a3[0].precolored


def test_temp_register_is_not_precolored():
    asm = "f:\n\tli\t$8, 3\n\taddu\t$9, $8, $8\n"
    ws = uopt.webs(asm)
    uopt.mark_precolored(ws, asm)
    assert all(not w.precolored for w in ws)


def test_rank_agreement_ignores_precolored_webs():
    a = uopt.Web(number=1, register="a3", occurrences=4, weight=4.0)
    a.precolored = True
    b = uopt.Web(number=2, register="t0", occurrences=2, weight=1.0)
    c = uopt.Web(number=3, register="t1", occurrences=2, weight=0.5)
    _con, pairs = uopt.rank_agreement([a, b, c])
    assert pairs == 1, "only the two genuinely allocated webs are comparable"
