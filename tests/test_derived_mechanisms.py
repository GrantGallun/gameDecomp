"""Fire tests for the two mechanisms derived by potential exploration (eval/results/retrodiction-20260922/).

Fixtures are recorded search nodes. evidence_site must reproduce the candidates that improved them when
compiled; frontend_type must fire on each byte-exact object the frontend gate rejected (compiled, each
`frontend_type:all` candidate was object-exact and passed the gate).
"""
import json
from pathlib import Path

import pytest

from solver import evidence_site, frontend_type_repair, regalloc_mutations

FIXTURES = Path(__file__).parent / "fixtures"
SITE = json.loads((FIXTURES / "evidence_site_cases.json").read_text())
FRONTEND = json.loads((FIXTURES / "frontend_type_cases.json").read_text())


@pytest.mark.parametrize("case", SITE, ids=[f"{c['function']}-{'+'.join(c['classes'])}" for c in SITE])
def test_evidence_site_fires_on_its_motivating_residual(case):
    made = {c for _l, c in evidence_site.variants(case["source"], case["function"], case["diff"],
                                                   case["source_attribution"])}
    assert case["improving"] and set(case["improving"]) <= made


def test_evidence_site_rewrites_a_member_access_to_the_stated_offset():
    # __sinf (2026-09-23): the offset is a member of a struct; the explicit form at the target offset improved
    # 4.73 points when compiled (eval/results/offset-access-20260923). IDO compiles both spellings identically.
    case = json.loads((FIXTURES / "evidence_site_member_offset.json").read_text())
    made = {c for _l, c in evidence_site.variants(case["source"], case["function"], case["diff"],
                                                   case["source_attribution"])}
    assert case["improving"] in made


def test_evidence_site_retypes_the_narrow_local_behind_a_surplus_mask():
    # drawMenuAsciiTextDefaultScale (2026-09-23): the candidate's extra mask came from a u8/u16 local assigned a
    # computed value (H13); retyping that local to s32 improved 12.94 points when compiled.
    case = json.loads((FIXTURES / "evidence_site_narrow_local.json").read_text())
    made = {c for _l, c in evidence_site.variants(case["source"], case["function"], case["diff"],
                                                   case["source_attribution"])}
    assert case["improving"] in made


def test_member_offset_rewrite_keeps_the_access_type():
    src = "typedef unsigned char u8;\nvoid f(Rec *p) {\n    x = p->pos.y;\n}\n"
    out = evidence_site._member_to_offset(src, 3, "lh", 0x2E)
    assert any("(*(s16 *)((u8 *)(&p->pos) + 0x2E))" in o for o in out)       # `.y`'s base is the struct p->pos
    assert not evidence_site._member_to_offset(src, 3, "addiu", 4)


def test_evidence_site_needs_an_attribution_of_this_source():
    case = SITE[0]
    assert not list(evidence_site.variants(case["source"], case["function"], case["diff"], None))
    other = dict(case["source_attribution"], source_sha256="0" * 64)
    assert not list(evidence_site.variants(case["source"], case["function"], case["diff"], other))
    assert not list(evidence_site.variants(case["source"], case["function"], "", case["source_attribution"]))


def test_evidence_site_reads_packed_attribution():
    from solver import source_attribution
    case = SITE[0]
    packed = source_attribution.pack_instructions(case["source_attribution"])
    plain = [c for _l, c in evidence_site.variants(case["source"], case["function"], case["diff"], case["source_attribution"])]
    assert [c for _l, c in evidence_site.variants(case["source"], case["function"], case["diff"], packed)] == plain


def test_evidence_site_never_edits_inside_a_comment():
    case = next(c for c in SITE if any(k.startswith("field:immediate") for k in c["classes"]))
    for candidate in {c for _l, c in evidence_site.variants(case["source"], case["function"], case["diff"],
                                                             case["source_attribution"])}:
        before = [l for l in case["source"].split("\n") if l.strip().startswith(("/*", "//", "*"))]
        after = [l for l in candidate.split("\n") if l.strip().startswith(("/*", "//", "*"))]
        assert before == after


@pytest.mark.parametrize("case", FRONTEND, ids=[c["function"] for c in FRONTEND])
def test_frontend_type_fires_on_each_gate_rejected_exact_object(case):
    made = dict(frontend_type_repair.variants(case["source"], case["function"], case["frontend"]))
    assert "frontend_type:all" in made and made["frontend_type:all"] != case["source"]


def test_frontend_type_rules():
    fe = lambda diag: {"passed": False, "diagnostics": diag}
    src = "#include \"common.h\"\n\nvoid f(s32 n) {\n    OSThread *t;\n    t = g(n);\n}\n"
    diag = ("candidate.c:5:7: error: incompatible integer to pointer conversion assigning to 'OSThread *' "
            "(aka 'struct OSThread_s *') from 's32' (aka 'long') [-Wint-conversion]\n"
            "candidate.c:5:9: error: implicit declaration of function 'g' [-Werror,-Wimplicit-function-declaration]\n")
    out = dict(frontend_type_repair.variants(src, "f", fe(diag)))["frontend_type:all"]
    assert "t = (OSThread *)(g(n));" in out
    assert "s32 g(s32);\n\nvoid f(s32 n)" in out
    proto = "void f() {\n}\n"
    assert "void f(void)" in dict(frontend_type_repair.variants(proto, "f", fe(
        "candidate.c:1:6: error: a function declaration without a prototype is deprecated in all versions of C\n")))["frontend_type:all"]
    assert not list(frontend_type_repair.variants(src, "f", {"passed": True, "diagnostics": diag}))
    assert not list(frontend_type_repair.variants(src, "f", None))


def test_frontend_type_retypes_inserted_prototype_parameters_from_passing_diagnostics():
    # finishCurrentRdpTask after `prototype@25`: the object is exact, the gate rejects the s32 parameters the
    # prototype rule guessed for non-identifier arguments (eval/results/frame-size-20260923/rdp_frontend.py).
    src = ("void osSendMesg(s32, s32, s32);\n\nvoid f(S *arg0) {\n    s32 sp1C;\n\n"
           "    osSendMesg(&arg0->queue14C, &sp1C, 1);\n    osSendMesg(arg0->t->doneQueue, arg0->t->doneMsg, 1);\n}\n")
    diag = ("candidate.c:6:16: error: incompatible pointer to integer conversion passing 'OSMesgQueue *' (aka "
            "'struct OSMesgQueue *') to parameter of type 's32' (aka 'long') [-Wint-conversion]\n"
            "    6 |     osSendMesg(&arg0->queue14C, &sp1C, 1);\n"
            "candidate.c:1:20: note: passing argument to parameter here\n"
            "candidate.c:6:33: error: incompatible pointer to integer conversion passing 's32 *' (aka 'long *') "
            "to parameter of type 's32' (aka 'long'); remove & [-Wint-conversion]\n"
            "candidate.c:1:25: note: passing argument to parameter here\n"
            "candidate.c:7:16: error: incompatible pointer to integer conversion passing 'OSMesgQueue *' (aka "
            "'struct OSMesgQueue *') to parameter of type 's32' (aka 'long') [-Wint-conversion]\n"
            "candidate.c:1:20: note: passing argument to parameter here\n"
            "candidate.c:7:39: error: incompatible pointer to integer conversion passing 'OSMesg' (aka 'void *') "
            "to parameter of type 's32' (aka 'long') [-Wint-conversion]\n"
            "candidate.c:1:25: note: passing argument to parameter here\n")
    out = dict(frontend_type_repair.variants(src, "f", {"passed": False, "diagnostics": diag}))["frontend_type:all"]
    assert out.startswith("void osSendMesg(OSMesgQueue *, void *, s32);\n")
    # a note pointing at a declaration that is not a prototype line, or a non-pointer type, changes nothing
    narrow = diag.replace("'OSMesgQueue *' (aka 'struct OSMesgQueue *')", "'s16'").replace(
        "'s32 *' (aka 'long *')", "'s16'").replace("'OSMesg' (aka 'void *')", "'s16'")
    assert not list(frontend_type_repair.variants(src, "f", {"passed": False, "diagnostics": narrow}))


def test_both_join_the_stream_only_with_evidence():
    case = FRONTEND[0]
    plain = [k for _l, k, _c in regalloc_mutations.variants(case["source"], case["function"], "")]
    assert "frontend_type" not in plain
    with_evidence = [k for _l, k, _c in regalloc_mutations.variants(case["source"], case["function"], "",
                                                                     evidence={"frontend": case["frontend"]})]
    assert with_evidence[0] == "frontend_type"
    site = SITE[0]
    kinds = {k for _l, k, _c in regalloc_mutations.variants(
        site["source"], site["function"], site["diff"], evidence={"source_attribution": site["source_attribution"]})}
    assert "evidence_site" in kinds


def test_scheduler_passes_the_verdict_only_to_generators_that_ask():
    from eval.search_replay import digest
    from eval.search_scheduler import Online
    seen = {}

    def compile_candidate(source, label, parent):
        return {"compiled": True, "exact": False, "score": 50.0, "diff": "d", "frontend": {"passed": True}}

    def two(source, diff):
        seen["two"] = diff
        return iter(())

    def three(source, diff, evidence=None):
        seen["three"] = evidence
        return iter(())
    ctx = {"task": "f", "initial_sha256": digest("x"), "partition": "evaluation", "target_sha256": "a" * 64,
           "compiler_sha256": "b" * 64, "generator_sha256": "c" * 64, "training_eligible": False,
           "assistance": "none"}
    for gen in (two, three):
        env = Online("x", compile_candidate, gen, ctx)
        env.start()
        env.expand("root")
    assert seen["two"] == "d" and seen["three"]["frontend"] == {"passed": True}


def test_evidence_site_reads_a_symbol_at_the_targets_addend():
    """resumeGameTask (2026-09-24): `%lo(G+4)` vs `%lo(G)` used to compare equal (the addend was dropped), so the
    stated residual produced no class and no proposal."""
    from solver import evidence_site
    assert evidence_site._fields("lw", ("v0", "%lo(gActiveGameTaskList+4)(v0)")) != \
        evidence_site._fields("lw", ("v0", "%lo(gActiveGameTaskList)(v0)"))
    source = "void f(void) {\n    struct GameTask *t = (struct GameTask *)gActiveGameTaskList;\n}\n"
    out = evidence_site._symbol_addend(source, 2, "lw    v0,%lo(gActiveGameTaskList+4)(v0)",
                                       "lw    v0,%lo(gActiveGameTaskList)(v0)")
    assert "    struct GameTask *t = (struct GameTask *)(*(void * *)((unsigned char *)&gActiveGameTaskList + 0x4));" \
        in "\n".join(out).split("\n")
    assert evidence_site._symbol_addend(source, 2, "lw    v0,%lo(a)(v0)", "lw    v0,%lo(b)(v0)") is None
