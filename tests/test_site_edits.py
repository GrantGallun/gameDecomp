"""solver.site_edits must FIRE on the residuals it was written for (CLAUDE.md, the fifth rule).

Fixtures are real campaign baselines (retained source, oracle diff, verified compiler line records)
captured 2026-09-29; the expected edits are the hand fixes that certified byte-exact that day.
"""
import json
from pathlib import Path

from solver import site_edits

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "site_edits_fires.json").read_text())


def _proposals(name):
    case = FIXTURES[name]
    edits, receipt = site_edits.propose(case["source"], name, case["diff"], case["attribution"])
    return case["source"], edits, receipt


def test_split_constant_literal_fires():
    # lui 0xff80 / ori 0x1 in the target against lui 0xff7f / ori 0xffff: the source literal moves by +2.
    source, edits, receipt = _proposals("updateEndingJamSlideLeftToMarker")
    assert 2 in receipt["literal_deltas"]
    assert any(e.apply(source).count("< -0x7FFFFF") == 1 for e in edits)


def test_store_signedness_fires_grouped_across_both_sites():
    # li 0xff / li 0x80 against li -1 / li -0x80: both byte stores are s8 where the target is u8.
    source, edits, _ = _proposals("enqueueSoundEffect")
    grouped = [e for e in edits if e.also and e.kind == "type" and e.text == "u8"]
    assert grouped, "the same type change at both stores must be offered as one edit"
    fixed = grouped[0].apply(source)
    assert "(*(u8 *)((unsigned char *)temp_v1 + 0x2))" in fixed
    assert "(*(u8 *)((unsigned char *)temp_v1 + 0x3))" in fixed


def test_stride_scale_fires_from_shift_delta():
    # sll 0x4 in the target against sll 0x2: the subscript is scaled by 4 at both calls.
    source, edits, receipt = _proposals("waitForCourseGateTrigger")
    assert receipt["scale_factors"] == [4]
    assert any(e.kind == "scale" and e.also for e in edits)


def test_off_by_one_only_at_comparisons():
    source, edits, _ = _proposals("waitForCourseGateTrigger")
    # sound ids and volumes on the call lines are not comparison bounds
    assert not any(e.kind == "literal" and e.label.startswith(("0x16", "0x7F", "0x1B", "0x32")) for e in edits)


def test_decline_carries_a_reason():
    case = FIXTURES["enqueueSoundEffect"]
    edits, receipt = site_edits.propose(case["source"], "enqueueSoundEffect", case["diff"], None)
    assert edits == [] and receipt["declined"]


def test_readback_fires_on_address_materialisation():
    # target `lui a0 / addiu a0 / sw v0,0(a0)` against our folded `lui at / sw v0,%lo(g)(at)`:
    # re-reading the stored global keeps its address in a register (IDO probe, 2026-09-29).
    source, edits, _ = _proposals("spawnEndingCreditsSmallBurst")
    readback = [e for e in edits if e.kind == "readback"]
    assert readback
    fixed = readback[0].apply(source)
    assert "(*(s16 *)((u8 *)((*((void **)&gActiveMenuTask))) + 0x18)) = arg0;" in fixed


def test_byte_stride_spelling_fires_from_target_shift():
    source, edits, receipt = _proposals("waitForCourseGateTrigger")
    assert receipt["strides"] == [16]
    assert any(e.kind == "spelling" and e.also and "* 16)" in e.text for e in edits)


def test_distances_separate_registers_from_instructions():
    diff = "\n".join(["--- a", "+++ b", "@@ -1,3 +1,3 @@", " jr    ra",
                      "-lui    a0,%hi(g)", "+lui    v1,%hi(g)", " nop"])
    assert site_edits.distances(diff) == (0, 1)
    diff = "\n".join(["--- a", "+++ b", "@@ -1,3 +1,2 @@", " jr    ra", "-addiu    v1,v1,8", " nop"])
    assert site_edits.distances(diff) == (1, 0)


def test_copy_direction_fires_on_address_materialisation():
    # `t = call(); G = t;` folds the store to %lo; `G = call(); t = G;` keeps &G in a0 (IDO probe
    # 2026-09-29). Certified exact on 5 spawnEndingCredits* functions with this one rewrite.
    source, edits, _ = _proposals("spawnEndingCreditsSmallBurst")
    copydir = [e for e in edits if e.kind == "copydir"]
    assert copydir
    fixed = copydir[0].apply(source)
    assert "*((void **)&gActiveMenuTask) = createCallbackTask(initEndingCreditsSmallBurst, 0, 0x64);" in fixed
    assert "temp_v0 = *((void **)&gActiveMenuTask);" in fixed
    assert "temp_v0 = createCallbackTask" not in fixed


def test_copy_direction_declines_across_a_call():
    source = ("void f(void) {\n    void *t;\n    t = make();\n    other();\n    gT = t;\n}\n")
    masked = site_edits.c89._mask(source)
    region = site_edits.code_shapes._body(source, "f")
    line = source.splitlines(keepends=True)
    start = sum(len(l) for l in line[:4])
    stop = start + len(line[4])
    assert site_edits._copy_direction_edits(source, masked, 5, start, stop, region) == []


def test_declaration_order_fires_on_frame_slot_faults():
    diff = "\n".join(["--- a", "+++ b", "@@ -1,2 +1,2 @@", "-sw    v0,0x1c(sp)", "+sw    v0,0x18(sp)"])
    assert site_edits.stack_slot_faults(diff) == 1
    source = "void f(void) {\n    void *sp1C;\n    void *temp_v0;\n\n    temp_v0 = g();\n}\n"
    region = site_edits.code_shapes._body(source, "f")
    [edit] = site_edits._declaration_order_edits(source, site_edits.c89._mask(source), region, diff)
    assert "    void *temp_v0;\n    void *sp1C;\n" in edit.apply(source)


def test_copy_direction_sees_through_address_holding_pointer():
    source = ("void f(void) {\n    void **ptr;\n    void *t;\n\n    t = make(1);\n    ptr = &gT;\n"
              "    *ptr = t;\n    use(t);\n}\n")
    masked = site_edits.c89._mask(source)
    region = site_edits.code_shapes._body(source, "f")
    lines = source.splitlines(keepends=True)
    start = sum(len(l) for l in lines[:6])
    [edit] = site_edits._copy_direction_edits(source, masked, 7, start, start + len(lines[6]), region)
    fixed = edit.apply(source)
    assert "gT = make(1);\n    t = gT;" in fixed and "t = make(1);" not in fixed.replace("gT = make(1);", "")


# --- composition with branch_shape and family interleaving (2026-09-29) ------

def test_propose_offers_branch_shape_repairs_without_attributed_lines():
    # MusStop's var_at is the motivating at_inline case; the site search must reach it too, not only
    # register search (eval/results/branch-routing-20260929).
    import importlib.util
    spec = importlib.util.spec_from_file_location("tbs", Path(__file__).with_name("test_branch_shape.py"))
    tbs = importlib.util.module_from_spec(spec); spec.loader.exec_module(tbs)
    MUSSTOP = tbs.MUSSTOP
    edits, receipt = site_edits.propose(MUSSTOP, "MusStop", "", None)
    assert receipt["shape_edits"] >= 1
    shape = [e for e in edits if e.kind.startswith("shape:")]
    assert shape and "var_at" not in shape[0].apply(MUSSTOP)


def test_interleaving_gives_every_family_an_early_slot():
    E = site_edits.Edit
    edits = [E("literal", str(i), 1, 0, 1, "x") for i in range(30)] + [E("decl", "d", 1, 0, 1, "y"),
                                                                       E("type", "t", 1, 0, 1, "z")]
    out = site_edits._interleaved(edits)
    assert [e.kind for e in out[:3]] == ["literal", "decl", "type"]
    assert [e.label for e in out if e.kind == "literal"] == [str(i) for i in range(30)]


def test_shape_repairs_are_a_priority_lane():
    # chain-vs-search-20260929: interleaved as one family among six, the shape repairs that were the only moves
    # on osMotorStart got one slot in six and the search ran out of budget before composing them.
    import importlib.util
    spec = importlib.util.spec_from_file_location("tuc", Path(__file__).with_name("test_unaligned_copy.py"))
    tuc = importlib.util.module_from_spec(spec); spec.loader.exec_module(tuc)
    edits, _ = site_edits.propose(tuc.SRC, "osMotorStart", tuc.DIFF, None)
    kinds = [e.kind.startswith("shape:") for e in edits]
    assert kinds[0] and kinds == sorted(kinds, reverse=True)      # every shape edit before every other


def test_default_budget_reaches_the_third_level():
    # chain-vs-search-20260929: budget 48 = 2 x per_step 24 left depth 3 unreachable; osMotorStart's third edit
    # (copy-back after copy and loop) was never tried. Each level here has more edits than per_step.
    from types import SimpleNamespace
    levels = []

    def fake_propose(src, fn, diff, attr, **kw):
        depth = src.count("+")
        return [site_edits.Edit("literal", f"e{depth}.{k}", 1, len(src), len(src), "+" if k == 0 else f"+x{k}")
                for k in range(30)], {}

    def score(code, label, parent):
        levels.append(code.count("+"))
        return SimpleNamespace(compiled=True, exact=False, score=50.0 + code.count("+"), source_attribution=None,
                               diff="--- t\n+++ c\n@@ -1,1 +1,1 @@\n-a\n+" + "b" * max(1, 5 - code.count("+")))
    original = site_edits.propose
    site_edits.propose = fake_propose
    try:
        site_edits.search(score, "S", "f", key=lambda a: (-a.score,))    # each deeper child is better
    finally:
        site_edits.propose = original
    assert max(levels) >= 3


def test_search_reports_a_baseline_that_is_already_exact(monkeypatch):
    # 2026-09-30: the loop broke on an exact baseline and returned "exact": False (setCurrentGameTaskCallback)
    from types import SimpleNamespace
    from solver import workspace
    monkeypatch.setattr(workspace, "repair_complete", lambda a: a.exact)
    score = lambda code, label, parent=None: SimpleNamespace(compiled=True, exact=True, score=100.0, diff="",
                                                             source_attribution=None)
    out = site_edits.search(score, "void f(void) {\n}\n", "f")
    assert out["exact"] and out["compiles"] == 0
