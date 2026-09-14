import json
import sqlite3

import pytest

from eval import differential_repair_pilot as pilot
from solver import mips_differential as differential
from solver.workspace import Attempt


def _attempt(score=50.0):
    return Attempt(True, score, False, "", "", "")


def _result(target_asm, candidate_asm):
    return differential.run_suite(
        target_asm, candidate_asm,
        (differential.TestCase("case", 1),))[0]


def _named_result(name, target_asm, candidate_asm):
    return differential.run_suite(
        target_asm, candidate_asm,
        (differential.TestCase(name, 1),))[0]


@pytest.mark.parametrize("plateau,recovery", [(False, False), (True, False), (False, True)])
def test_exactness_panel_tries_exact_sibling_after_first_improvement(
        tmp_path, monkeypatch, plateau, recovery):
    assembly = "li v0,1\njr ra\nnop\n"
    root_source = "int f(void) { return 1; }"
    better_source = "int f(void) { return (int)1; }"
    exact_source = "int f(void) { int result = 1; return result; }"
    source_path = tmp_path / "root.c"
    source_path.write_text(root_source)
    (tmp_path / "target_object_dump_normalized.s").write_text(assembly)
    monkeypatch.setattr(pilot.refine, "ensure_schema", lambda conn: None)
    monkeypatch.setattr(pilot.workspace, "bootstrap", lambda *args: tmp_path)
    monkeypatch.setattr(pilot.workspace, "target_asm", lambda *args: assembly)
    monkeypatch.setattr(pilot.workspace, "semantic_assembly",
                        lambda text, path: text)
    monkeypatch.setattr(pilot.project_headers, "prompt_context",
                        lambda *args: "")
    monkeypatch.setattr(pilot.exactness_gradient, "receipt_history",
                        lambda *args, **kwargs: ())
    monkeypatch.setattr(
        pilot, "deterministic_exactness_candidates",
        lambda source, *args, **kwargs: (
            pilot.principle_variants.Variant("first-improvement", better_source),
            pilot.principle_variants.Variant("exact-sibling", exact_source),
        )[:1 if plateau else 2] if source == root_source else (
            (pilot.principle_variants.Variant("plateau-exit", exact_source),)
            if plateau and source == better_source else ()))
    scored = []
    if recovery:
        monkeypatch.setattr(pilot.workspace, "assert_uncontaminated", lambda *args: None)
        monkeypatch.setattr(pilot.proposal_recovery, "load", lambda *a, **kw: (
            pilot.proposal_recovery.SavedProposal(99, 100, root_source, json.dumps({
                "kind": "expression", "hypothesis": "materialize result",
                "edits": [{"old": "return 1;", "new": "int result = 1; return result;"}]})),))

    def score(ws, repo, tag, source, **kwargs):
        scored.append((source, kwargs["parent_attempt_id"]))
        (ws / f"{tag}_object_dump_normalized.s").write_text(assembly)
        index = (root_source, better_source, exact_source).index(source)
        return Attempt(True, (50, 45 if plateau else 75, 100)[index], index == 2,
                       "", "", "", receipt_id=101 + index)

    monkeypatch.setattr(pilot.workspace, "score", score)
    receipt = pilot.run(
        repo=tmp_path, db=tmp_path / "attempts.sqlite",
        source_path=source_path, source_parent_attempt_id=100,
        output=tmp_path / "receipt.json", best_source_out=tmp_path / "best.c",
        model="unused", endpoint="unused", rounds=1, timeout=1, think="high",
        num_thread=1, temperature=0, diagnosis_num_predict=1,
        patch_num_predict=1, patch_retries=0, compiler_retries=0,
        max_stalls=1, seed=1, cache_dir=None, function="f",
        cases=(differential.TestCase("case", 1),), call_arities={},
        return_registers=("v0",), deterministic_only=True, exactness_expansions=2,
        compiler_response_policy=pilot.transition_policy.TransitionPolicy(()))

    assert receipt["result"]["exact"]
    if recovery:
        assert scored == [(root_source, 100), (exact_source, 100)]
        assert receipt["proposal_recovery"][0]["accepted"]
        assert receipt["recorded_tokens"] == 0
        return
    assert scored == [(root_source, 100), (better_source, 101),
                      (exact_source, 102 if plateau else 101)]
    rows = receipt["deterministic_exactness"]
    assert len(rows) == 2
    assert receipt["candidate_frontier"]["max_expansions_per_round"] == 2
    diversity = receipt["compiler_output_diversity"]
    assert sum(wave["tested"] for wave in diversity) == 2
    assert all(wave["distinct_interpreter_inputs"] == 1 for wave in diversity)
    assert all(wave["distinct_nonparent_inputs"] == 0 for wave in diversity)
    assert all(wave["all_compiled_outputs_equal_parent"] for wave in diversity)
    assert all(row["candidate_assembly_sha256"] == row["parent_assembly_sha256"]
               for row in rows)
    if plateau:
        assert not rows[0]["accepted_for_next_round"]
        assert rows[0]["offered_to_frontier"]
    else:
        assert rows[0]["source_path"] != rows[1]["source_path"]
    certificate = receipt["result"]["semantic_certificate"]
    assert certificate["source_sha256"] == pilot._sha(exact_source)
    assert certificate["all_cases_passed"]
    assert not certificate["universal_equivalence_proven"]
    assert receipt["recorded_tokens"] == 0


def test_diagnosis_requires_completion_even_if_truncated_text_has_labels():
    diagnosis = "\n".join([
        "1. NEXT BAD OBSERVABLE: store width", "2. TARGET VALUE/ADDRESS PROVENANCE: sh",
        "3. CANDIDATE VALUE/ADDRESS PROVENANCE: sb", "4. SOURCE-LEVEL CAUSE: byte field",
        "5. MINIMAL PATCH PLAN: change field to u16"])
    assert pilot.diagnosis_complete(diagnosis, {"done_reason": "stop"})
    typography = diagnosis.replace("SOURCE-LEVEL", "SOURCE\u2011LEVEL")
    typography = "\n".join("**" + line + "**" for line in typography.splitlines())
    assert pilot.diagnosis_complete(typography, {"done_reason": "stop"})
    unnumbered = "\n".join(line[3:].replace(": ", ":\n", 1)
                           for line in diagnosis.splitlines())
    assert pilot.diagnosis_complete(unnumbered, {"done_reason": "stop"})
    assert not pilot.diagnosis_complete(diagnosis, {"done_reason": "length"})
    assert not pilot.diagnosis_complete(diagnosis, {"_fell_back_to_thinking": True})
    assert not pilot.diagnosis_complete("unfinished", {"done_reason": "stop"})


def test_width_repair_cannot_move_already_exact_struct_field():
    source = "struct State {\n u8 pad[22];\n u8 active;\n};\n"
    result = _result("sh zero,0x16(a0)\njr ra\nnop",
                     "sb zero,0x16(a0)\njr ra\nnop")
    good = source.replace("u8 active", "u16 active")
    pilot.validate_observable_locks(source, good, [result])
    with pytest.raises(ValueError, match="width-only repair moved"):
        pilot.validate_observable_locks(source, good.replace("pad[22]", "pad[20]"), [result])


def test_binary_observed_offsets_flatten_parameter_layouts():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table functions (addr integer, name text)")
    conn.execute(
        "create table evidence (kind text, func_addr integer, base text, "
        "offset integer, width integer, signed integer, is_load integer)")
    conn.execute("insert into functions values (1, 'f')")
    conn.executemany(
        "insert into evidence values ('mem_access', 1, ?, ?, ?, 1, 1)",
        [("param0", 0x24, 4), ("param0", 0x6A, 1),
         ("param1", 0x24, 2)])

    observed = pilot._binary_observed_offsets(conn, "f")

    assert observed == {0x24: 4, 0x6A: 1}


def test_prompt_contains_debugger_divergence_and_not_reference_source():
    target = "sw zero,0(a0)\njr ra\nnop"
    candidate = "sw zero,4(a0)\njr ra\nnop"
    result = _result(target, candidate)
    prompt = pilot.build_prompt(
        target, "void f(void *p) { }", _attempt(), [result], [])
    assert "first divergence: write #0 differs" in prompt
    assert "finished/reference target C" in prompt
    assert "Return JSON only" in prompt


def test_two_phase_prompts_include_candidate_trace_then_recorded_diagnosis():
    target = "sw zero,0(a0)\njr ra\nnop"
    candidate = "sw zero,4(a0)\njr ra\nnop"
    result = _result(target, candidate)
    diagnosis_prompt = pilot.build_diagnosis_prompt(
        target, candidate, "void f(void *p) { }", _attempt(), [result], [])
    completed_diagnosis = """\
1. NEXT BAD OBSERVABLE: wrong store
2. TARGET VALUE/ADDRESS PROVENANCE: target offset zero
3. CANDIDATE VALUE/ADDRESS PROVENANCE: candidate offset four
4. SOURCE-LEVEL CAUSE: wrong field
5. MINIMAL PATCH PLAN: replace the field expression
"""
    patch_prompt = pilot.build_patch_prompt(
        "void f(void *p) { }", completed_diagnosis, [result], [])

    assert "CURRENT CANDIDATE MIPS STATIC CONTEXT" in diagnosis_prompt
    assert "full static body omitted" in diagnosis_prompt
    assert "sw zero,4(a0)" in diagnosis_prompt
    assert "causal backward-slice evidence" in diagnosis_prompt
    assert "Do not emit JSON yet" in diagnosis_prompt
    assert "LOGIC FIRST" in diagnosis_prompt
    assert "SOURCE-LEVEL CAUSE: wrong field" in patch_prompt
    assert "Return JSON only" in patch_prompt
    assert '"spans"' in patch_prompt
    assert "1 | void f(void *p) { }" in patch_prompt
    assert "N*sizeof(T)" in diagnosis_prompt


def test_mechanical_bridge_decodes_mips_bytes_and_c_pointer_scaling():
    source = """\
s32 compress(u8 *src, u16 *dst) {
    u16 *cursor;
    cursor = dst + 2;
    cursor += 2;
    *cursor = src[0];
    return *cursor;
}
"""
    feedback = """\
target: arg2+0x2/2=0x16 at i48
candidate: arg2+0x8/2=0x16 at i46
executed target window:
  dyn#010/i010 addiu t5,t5,0x2 | in t5=0x12000002 | out t5=0x12000004
  dyn#011/i011 sh t9,-0x2(t5) | store arg2+0x2/2=0x16
executed candidate window:
  dyn#010/i010 addiu t3,t3,0x4 | in t3=0x12000004 | out t3=0x12000008
  dyn#011/i011 sh a0,0(t3) | store arg2+0x8/2=0x16
"""

    bridge = pilot.mechanical_opcode_to_c_bridge(source, feedback)

    assert "`sh` stores exactly 2 bytes" in bridge
    assert "`addiu t5,t5,0x2` computes `t5 = t5 +2` raw byte" in bridge
    assert "`sh t9,-0x2(t5)` accesses exactly 2 byte(s)" in bridge
    assert "the slash is not division or C pointer scaling" in bridge
    assert "target raw byte offset +2, candidate raw byte offset +8" in bridge
    assert "`cursor = dst + 2`" in bridge
    assert "raw address delta is +4 byte(s), not +2 byte(s)" in bridge
    assert "`cursor += 2`" in bridge
    assert "First textual store recurrence through `cursor`" in bridge
    assert "= base +8 raw byte(s)" in bridge
    assert "store through `*cursor` uses raw address `cursor +0`" in bridge
    assert "`cursor[-1]` would use -2 bytes" in bridge


def test_mechanical_bridge_exposes_actual_layout_behind_offset_comment():
    source = """\
struct AssetHandle {
    u16 handle; /* stored at offset 0x5A */
    u16 padding0;
};
"""

    bridge = pilot.mechanical_opcode_to_c_bridge(
        source, "dyn#001/i001 sh v0,0x5a(v1)")

    assert "`AssetHandle.handle` compiles at byte offset 0x0" in bridge
    assert "trailing-comment=0x5a: MISMATCH" in bridge
    assert "computed size=4 bytes" in bridge


def test_mechanical_bridge_tracks_explicit_char_pointer_byte_updates():
    source = """\
void f(u16 *dst) {
    u16 *t5;
    t5 = dst + 1;
    t5 = (u16 *)((char *)t5 + 2);
    *t5 = 7;
}
"""

    feedback = """\
target: arg2+0x2/2=0x16 at i48
candidate: arg2+0x4/2=0x16 at i46
"""
    bridge = pilot.mechanical_opcode_to_c_bridge(source, feedback)

    assert "arithmetic is explicitly cast through `char *`" in bridge
    assert "+2 (update line 4)" in bridge
    assert "= base +4 raw byte(s)" in bridge
    assert "changing the relevant `*t5` store to `t5[-1]`" in bridge


def test_mechanical_bridge_distinguishes_predecrement_from_displacement():
    source = """\
void f(u16 *t5) {
    *--t5 = 7;
}
"""

    bridge = pilot.mechanical_opcode_to_c_bridge(
        source, "dyn#1/i1 sh t0,-2(t5)")

    assert "first mutates `t5` by -2 bytes" in bridge
    assert "does not mutate `t5`" in bridge
    assert "`t5[-1]` supplies the same -2-byte access displacement" in bridge


def test_diagnosis_prompt_contains_mechanical_opcode_to_c_bridge():
    target = "addiu t0,t0,2\nsh zero,0(t0)\njr ra\nnop"
    candidate = "addiu t0,t0,4\nsh zero,0(t0)\njr ra\nnop"
    result = _result(target, candidate)
    source = "void f(u16 *p) { p += 2; *p = 0; }"

    prompt = pilot.build_diagnosis_prompt(
        target, candidate, source, _attempt(), [result], [])

    assert "MECHANICAL OPCODE-TO-C BRIDGE" in prompt
    assert "raw address delta is +4 byte(s), not +2 byte(s)" in prompt
    assert "operand ledger" in prompt
    assert "complete raw-byte equation" in prompt


def test_patch_prompt_omits_incomplete_diagnosis_and_ends_with_proof():
    target = "sw zero,0(a0)\njr ra\nnop"
    candidate = "sw zero,4(a0)\njr ra\nnop"
    result = _result(target, candidate)
    repeated = ("Maybe the pointer is wrong. " * 2000)

    prompt = pilot.build_patch_prompt(
        "void f(u16 *p) { p += 2; *p = 0; }", repeated, [result], [])

    assert "Maybe the pointer is wrong." not in prompt
    assert "INCOMPLETE INVESTIGATOR OUTPUT OMITTED BY CONTROLLER" in prompt
    assert prompt.index("INVESTIGATOR DIAGNOSIS") < \
        prompt.index("AUTHORITATIVE MECHANICAL OPCODE-TO-C BRIDGE")
    assert "FINAL PATCH OBLIGATION" in prompt


def test_patch_retry_restates_schema_diagnosis_and_evidence():
    diagnosis = """\
1. NEXT BAD OBSERVABLE: candidate stored zero
2. TARGET VALUE/ADDRESS PROVENANCE: target constant one
3. CANDIDATE VALUE/ADDRESS PROVENANCE: candidate constant zero
4. SOURCE-LEVEL CAUSE: wrong initializer
5. MINIMAL PATCH PLAN: replace zero with one
"""
    prompt = pilot.build_patch_retry_prompt(
        "int value = 0;\n", '{"spans": [[1, 1]]}',
        "source span must be an object or 3-item array",
        diagnosis,
        "candidate stored 0; target stored 1")

    assert '"start_line": 12' in prompt
    assert "do not return two-item arrays" in prompt
    assert "replace zero with one" in prompt
    assert "candidate stored 0; target stored 1" in prompt
    assert "earlier patch merely repeated CURRENT C" in prompt
    assert "AUTHORITATIVE MECHANICAL OPCODE-TO-C BRIDGE" in prompt


def test_patch_retry_receives_exactness_feed_and_abi_map():
    diff = ("--- target\n+++ candidate\n-addiu t5,a2,2\n"
            "+addiu t3,a2,2\n-sh t9,-2(t5)\n+sh a0,-2(t3)")
    source = "void f(u8 *src, s32 len, u16 *dst) { dst[1] = src[0]; }\n"

    prompt = pilot.build_patch_retry_prompt(
        source, '{"spans":[]}', "empty edit", "", "", diff,
        rejected=["pointer-stride hypothesis contradicted by residual"])

    assert "DETERMINISTIC EXACTNESS GRADIENT" in prompt
    assert "a2 = C parameter `dst`" in prompt
    assert "pointer-stride hypothesis contradicted" in prompt
    assert "Discard the rejected edit" in prompt
    assert '"different C"' not in prompt
    assert '"start_line": 12' not in prompt


def test_register_only_patch_uses_dedicated_source_shape_prompt():
    assembly = "jr ra\nnop"
    passed = _result(assembly, assembly)
    diff = ("--- target\n+++ candidate\n-addiu t5,a2,2\n"
            "+addiu t3,a2,2\n-sh t9,-2(t5)\n+sh a0,-2(t3)")
    source = "void f(u8 *src, s32 len, u16 *dst) { dst[1] = src[0]; }\n"

    prompt = pilot.build_patch_prompt(
        source, "incorrect pointer diagnosis", [passed], [], diff)

    assert "source-shape actuation phase" in prompt
    assert "incorrect pointer diagnosis" not in prompt
    assert "DYNAMIC CAUSAL EVIDENCE" not in prompt
    assert "Never change `dst + 1` to `dst + 2`" in prompt
    assert "Never replace an executable assignment with a" in prompt
    assert "declaration, and never emit an unused new local" in prompt
    assert "a2 = C parameter `dst`" in prompt


def test_compiler_retry_demands_a_different_complete_span():
    prompt = pilot.build_compiler_fix_prompt(
        "int broken = ;\n", "candidate.c, line 1: Syntax Error",
        "retain the corrected pointer stride")

    assert '"start_line":12' in prompt
    assert "repeating its lines is a no-op" in prompt
    assert "reset to match the numbered C" in prompt
    assert '"new":""' in prompt
    assert "not an error report" in prompt


def test_two_phase_prompts_prefer_resynchronized_bundle_when_available():
    target = "sw zero,0(a0)\njr ra\nnop"
    candidate = "sw zero,4(a0)\njr ra\nnop"
    result = _result(target, candidate)
    bundle = (
        "RESYNCHRONIZED INDEPENDENT-DIVERGENCE BUNDLE\n"
        "CLUSTER 1: wrong first store\n"
        "CLUSTER 2: wrong later call")

    diagnosis = pilot.build_diagnosis_prompt(
        target, candidate, "void f(void *p) { }", _attempt(), [result], [],
        bundle)
    patch = pilot.build_patch_prompt(
        "void f(void *p) { }", "fix both expressions", [result], [],
        divergence_bundle=bundle)

    assert bundle in diagnosis
    assert bundle in patch
    assert "CROSS-CASE, FORCED-RESYNCHRONIZED" in diagnosis


def test_failed_semantic_prompt_omits_unexecuted_static_tail():
    target = "sw zero,0(a0)\nb 10\nnop\nli s7,1234\njr ra\nnop"
    candidate = "sw zero,4(a0)\nb 10\nnop\nli s7,5678\njr ra\nnop"
    result = _result(target, candidate)
    prompt = pilot.build_diagnosis_prompt(
        target, candidate, "void f(void) { }", _attempt(), [result], [])

    assert "full static body omitted" in prompt
    assert "li s7,1234" not in prompt
    assert "li s7,5678" not in prompt


def test_semantic_pass_switches_prompt_to_exact_residual():
    assembly = "sw zero,0(a0)\njr ra\nnop"
    result = _result(assembly, assembly)
    attempt = Attempt(
        True, 99.0, False, "- lw v0,0(a0)\n+ lw v0,4(a0)", "", "")

    diagnosis = pilot.build_diagnosis_prompt(
        assembly, assembly, "void f(void) { }", attempt, [result], [])
    patch = pilot.build_patch_prompt(
        "void f(void) { }", "fix declaration order", [result], [])

    assert "BYTE EXACTNESS" in diagnosis
    assert "- lw v0,0(a0)" in diagnosis
    assert "STATIC RESIDUAL CONTEXT" in diagnosis
    assert "CONTROLLER-DETERMINISTIC EXACTNESS FEED" in diagnosis
    assert "DETERMINISTIC EXACTNESS GRADIENT" in diagnosis
    assert assembly not in diagnosis
    assert "byte exactness is now the primary goal" in patch


def test_exactness_principles_map_signed_loads_and_moved_loads():
    diff = """\
-lw v0,0x44(s0)
 lw t5,0x1c(s0)
+lw v0,0x44(s0)
-lh t7,%lo(gFrameCounter)(t7)
+lhu t7,%lo(gFrameCounter)(t7)
-lb t6,0x14(s0)
+lbu t6,0x14(s0)
"""

    source = """\
#define RACE_PLAYER_44(p) (*(s32 *)((char *)(p) + 0x44))
void f(void *player) {
    RACE_PLAYER_YVEL(player) = RACE_PLAYER_44(player);
}
"""
    hints = pilot.exactness_principles(diff, source)

    assert "signed 16-bit C declaration" in hints
    assert "signed 8-bit C declaration" in hints
    assert "scheduling/evaluation-order residual" in hints
    assert "do not duplicate arithmetic" in hints
    assert "offset `0x44` is accessed through `RACE_PLAYER_44(...)`" in hints
    assert "RACE_PLAYER_YVEL(player) = RACE_PLAYER_44(player);" in hints

    retry_prompt = pilot.build_patch_retry_prompt(
        "void f(void *p) { use(p); }",
        '{"edits":[{"old":"missing","new":"fixed"}]}',
        "old substring occurs 0 times")
    assert "old substring occurs 0 times" in retry_prompt
    assert "one to twelve valid" in retry_prompt.replace("\n", " ")
    assert "void f(void *p) { use(p); }" in retry_prompt

    compiler_prompt = pilot.build_compiler_fix_prompt(
        "void f(void) { $(int)1; }", "Unknown character $ ignored",
        "preserve the corrected field dataflow")
    assert "Unknown character $ ignored" in compiler_prompt
    assert "preserve the corrected field dataflow" in compiler_prompt
    assert "CURRENT NONCOMPILING C" in compiler_prompt


def test_exactness_principles_recognize_register_only_statement_order():
    diff = """\
-lh t0,0x304(s0)
-lw t8,0x7c(s0)
-addiu t1,t0,1
-sh t1,0x304(s0)
-addiu t9,t8,0x16
-sw t9,0x7c(s0)
+lh t8,0x304(s0)
+lw t0,0x7c(s0)
+addiu t9,t8,1
+sh t9,0x304(s0)
+addiu t1,t0,0x16
+sw t1,0x7c(s0)
"""

    hints = pilot.exactness_principles(diff)

    assert "register-allocation-only residual" in hints
    assert "adjacent independent C statements" in hints


def test_register_only_residual_activates_bounded_statement_swaps():
    diff = """\
-lh t0,0x304(s0)
-lw t8,0x7c(s0)
-addiu t1,t0,1
-addiu t9,t8,0x16
+lh t8,0x304(s0)
+lw t0,0x7c(s0)
+addiu t9,t8,1
+addiu t1,t0,0x16
"""
    source = """\
typedef unsigned char u8;
typedef signed short s16;
typedef signed int s32;
#define STATE_TIMER(p) (*(s32 *)((u8 *)(p) + 0x7c))
#define UPDATE_TIMER(p) (*(s16 *)((u8 *)(p) + 0x304))
void update(void *player) {
    UPDATE_TIMER(player)++;
    STATE_TIMER(player) += 0x16;
}
"""

    variants = pilot.deterministic_exactness_candidates(
        source, "update", diff)

    assert len(variants) == 1
    assert variants[0].source.index("STATE_TIMER(player) += 0x16") < \
        variants[0].source.index("UPDATE_TIMER(player)++")
    # Structural residuals now retain the same bounded source alternatives.
    assert pilot.deterministic_exactness_candidates(
        source, "update", "-lw v0,0(a0)\n+lw v0,4(a0)")


def test_register_local_order_residual_uses_normalized_statement_search():
    diff = """\
-addiu t3,t3,1
-bne t6,t7,7c
+bne t6,t7,7c
+addiu t5,t5,1
"""
    source = """\
void compress(void) {
    a = 0;
    b = 0;
    consume();
    t9 = src[v0]; v1++; t5 = t5 + 2;
    t5[-1] = t9; v0++;
}
"""

    assert not pilot.register_operands_only(diff)
    assert pilot.register_or_local_order_only(diff)
    assert "REGISTER/LOCAL-ORDER-ONLY" in \
        pilot.exactness_residual_classification(diff)

    variants = pilot.deterministic_exactness_candidates(
        source, "compress", diff)

    assert variants
    assert any(variant.source.index("v0++") <
               variant.source.index("t5[-1] = t9")
               for variant in variants)


def test_identical_deleted_and_inserted_row_is_a_local_move():
    diff = "-sh zero,0xc2(a0)\n+sh zero,0xc2(a0)"
    source = """\
typedef struct { s16 left; s16 right; } Pair;
void f(Pair *pair, s16 value) {
    pair->left = 0;
    pair->right = value;
}
"""

    assert pilot.register_or_local_order_only(diff)
    assert pilot.deterministic_exactness_candidates(source, "f", diff)


def test_parameter_home_residual_activates_dead_parameter_reuse():
    diff = "-sw a0,0(sp)\n-move t7,a0\n+addiu v1,a0,0x400"
    source = """\
typedef signed short s16;
s16 f(s16 arg0) {
    s16 angle;
    angle = (arg0 + 0x400) & 0xFFF;
    return table[angle];
}
"""

    variants = pilot.deterministic_exactness_candidates(source, "f", diff)

    assert any(row.label == "reuse-parameter-arg0-for-angle"
               for row in variants)


def test_deterministic_exactness_candidates_include_split_epoch_family():
    source = """\
void f(s32 limit) {
    s32 value;
    s32 out;
    value = limit;
    out = 0;
    for (;;) {
        value = 0;
        while (value < limit) {
            value++;
        }
        out = value;
        value = limit - out;
    }
}
"""
    diff = ("--- target\n+++ candidate\n-move t3,zero\n"
            "+move t4,zero\n-addiu t4,t4,1\n+addiu t5,t5,1")

    variants = pilot.deterministic_exactness_candidates(source, "f", diff)

    assert any("split value epoch value E2" in row.label
               for row in variants)


def test_transition_policy_reorders_but_does_not_remove_exactness_panel():
    source = """\
void f(s32 limit) {
    s32 value;
    s32 out;
    value = limit;
    out = 0;
    for (;;) {
        value = 0;
        while (value < limit) {
            value++;
        }
        out = value;
        value = limit - out;
    }
}
"""
    diff = ("--- target\n+++ candidate\n-move t3,zero\n"
            "+move t4,zero\n-addiu t4,t4,1\n+addiu t5,t5,1")

    class Estimate:
        def __init__(self, priority):
            self.priority = priority

        def rank_key(self):
            return (self.priority,)

    class PreferRegister:
        def estimate(self, action, state, **kwargs):
            return Estimate(int(action.startswith("register-")))

    original = pilot.deterministic_exactness_candidates(
        source, "f", diff, max_variants=12)
    ranked = pilot.deterministic_exactness_candidates(
        source, "f", diff, max_variants=12, policy=PreferRegister())

    assert ranked[0].label.startswith("register-")
    assert {(row.label, row.source) for row in ranked} == {
        (row.label, row.source) for row in original}


def test_deterministic_semantic_candidate_materializes_address_equation():
    source = """\
void f(u16 *dst) {
    u16 *t5;
    t5 = dst + 1;
    t5 = (u16 *)((char *)t5 + 2);
    *t5 = 7;
}
"""
    feedback = """\
target: arg2+0x2/2=0x7 at i9
candidate: arg2+0x4/2=0x7 at i8
"""

    variants = pilot.deterministic_semantic_candidates(source, feedback)

    assert len(variants) == 1
    assert variants[0].label == "equation-derived-store-index-t5--1"
    assert "t5[-1] = 7;" in variants[0].source


def test_deterministic_semantic_candidate_removes_predecrement_side_effect():
    source = "void f(u16 *t5) { *--t5 = 7; }"
    feedback = "dyn#1/i1 sh t0,-2(t5)"

    variants = pilot.deterministic_semantic_candidates(source, feedback)

    assert len(variants) == 1
    assert variants[0].label == "nonmutating-store-displacement-t5--1"
    assert "t5[-1] = 7" in variants[0].source


def test_alias_divergence_activates_early_rhs_materialization():
    source = """\
typedef unsigned short u16;
typedef struct { short left; short right; } Pair;
int f(Pair *pair, unsigned char *input) {
    pair->left = 0;
    pair->right = (u16) *input;
    return 1;
}
"""
    feedback = (
        "case: base-pa1alias10000000\nfirst divergence: target value "
        "differs from candidate value")

    variants = pilot.deterministic_semantic_candidates(
        source, feedback, "f")

    assert any(variant.label ==
               "materialize-aliased-rhs-before-disjoint-write"
               for variant in variants)


def test_diagnosis_prompt_includes_partial_struct_layout_audit():
    source = """\
typedef struct {
    char pad[0x2ee];
    s16 unk2EE;
    s16 unk2F6;
} Player;
void f(Player *p) { p->unk2F6 = 0; }
"""
    result = _result("jr ra\nnop", "jr ra\nnop")
    attempt = Attempt(True, 99.0, False, "-sh zero,0x2f6(a0)\n"
                      "+sh zero,0x2f0(a0)", "", "")

    prompt = pilot.build_diagnosis_prompt(
        "jr ra\nnop", "jr ra\nnop", source, attempt, [result], [])

    assert "LOCAL PARTIAL-STRUCT LAYOUT AUDIT" in prompt
    assert "Player.unk2F6" in prompt
    assert "laid out at `0x2f0`" in prompt


def test_source_span_patch_resolves_location_without_copied_old_text():
    source = """\
typedef struct Player {
    int repeated;
    int repeated;
} Player;
void f(Player *p) {
    p->repeated = 1;
}
"""
    response = """{
      "kind": "layout",
      "hypothesis": "insert the missing field padding",
      "spans": [{
        "start_line": 3,
        "end_line": 3,
        "new": "    char pad[6];\\n    int repeated;"
      }]
    }"""

    proposal, edited, edit_format = pilot.parse_and_apply_patch(
        response, source)

    assert edit_format == "source-span"
    assert proposal.kind == "layout"
    assert "char pad[6];\n    int repeated;\n" in edited
    assert edited.count("int repeated;") == 2


def test_source_span_patch_rejects_whole_file_and_multiple_intervals():
    source = "int a;\nint b;\n"
    whole = ('{"kind":"other","hypothesis":"bad","spans":'
             '[{"start_line":1,"end_line":2,"new":"int c;"}]}')
    overlapping = ('{"kind":"other","hypothesis":"bad","spans":['
                   '{"start_line":1,"end_line":2,"new":"int c;"},'
                   '{"start_line":2,"end_line":2,"new":"int d;"}]}')

    import pytest
    with pytest.raises(ValueError, match="whole-file"):
        pilot.parse_and_apply_patch(whole, source)
    with pytest.raises(ValueError, match="overlap"):
        pilot.parse_and_apply_patch(overlapping, source)


def test_source_spans_normalize_numeric_arrays_and_line_lists():
    source = "int a;\nint b;\nint c;\nint d;\n"
    response = """{
      "kind": "declarations",
      "spans": [
        ["1", "1", ["unsigned int a;"]],
        ["3", "3", "unsigned int c;"]
      ]
    }"""

    proposal, edited, edit_format = pilot.parse_and_apply_patch(
        response, source)

    assert edit_format == "source-spans"
    assert len(proposal.edits) == 2
    assert edited == "unsigned int a;\nint b;\nunsigned int c;\nint d;\n"


def test_source_spans_allow_medium_multi_site_repair():
    source = "".join(f"int value_{index};\n" for index in range(20))
    spans = [
        {"start_line": index + 1, "end_line": index + 1,
         "new": f"unsigned int value_{index};"}
        for index in (0, 2, 4, 6, 8, 10)
    ]
    response = json.dumps({
        "kind": "declarations", "hypothesis": "fix related widths",
        "spans": spans,
    })

    proposal, edited, edit_format = pilot.parse_and_apply_patch(
        response, source)

    assert edit_format == "source-spans"
    assert len(proposal.edits) == 6
    assert "unsigned int value_10;" in edited


def test_source_span_strips_accidentally_copied_line_prefixes():
    source = "int a;\nint b;\nint c;\n"
    response = ('{"kind":"declarations","spans":['
                '{"start_line":2,"end_line":2,'
                '"new":"   2 | unsigned int b;"}]}')

    _proposal, edited, _format = pilot.parse_and_apply_patch(response, source)

    assert edited == "int a;\nunsigned int b;\nint c;\n"


def test_source_span_preserves_unrelated_statements_on_same_line():
    source = "v1++; t5 += 2; v0++;\nreturn v1;\n"
    response = ('{"kind":"expression","spans":['
                '{"start_line":1,"end_line":1,"new":"t5 += 1;"}]}')

    _proposal, edited, edit_format = pilot.parse_and_apply_patch(
        response, source)

    assert edit_format == "source-span"
    assert edited == "v1++; t5 += 1; v0++;\nreturn v1;\n"


def test_source_span_declines_ambiguous_sibling_splice():
    source = "t5++; t5 += 2; v0++;\nreturn v0;\n"
    response = ('{"kind":"expression","spans":['
                '{"start_line":1,"end_line":1,"new":"t5 += 1;"}]}')

    _proposal, edited, _edit_format = pilot.parse_and_apply_patch(
        response, source)

    assert edited == "t5 += 1;\nreturn v0;\n"


def test_source_span_preserves_unique_siblings_inside_multiline_span():
    source = """\
if (literal) {
    v1++; t5 += 2; v0++;
    *t5 = value;
}
return v1;
"""
    response = json.dumps({
        "kind": "expression",
        "spans": [{
            "start_line": 2,
            "end_line": 3,
            "new": "    t5 += 1;\n    *t5 = value;",
        }],
    })

    _proposal, edited, _edit_format = pilot.parse_and_apply_patch(
        response, source)

    assert "    v1++; t5 += 1; v0++;\n" in edited
    assert "    *t5 = value;\n" in edited


def test_source_span_rejects_new_explanatory_comments():
    source = "int value = 0;\n"
    response = ('{"kind":"expression","spans":['
                '{"start_line":1,"end_line":1,'
                '"new":"// fix this\\nint value = 1;"}]}')

    with pytest.raises(ValueError, match="explanatory comments"):
        pilot.parse_and_apply_patch(response, source)


def test_behavior_key_prefers_semantic_pass_over_byte_score():
    target = "sw zero,0(a0)\njr ra\nnop"
    good = _result(target, target)
    bad = _result(target, "sw zero,4(a0)\njr ra\nnop")
    assert pilot.behavior_key([good], _attempt(1.0)) > \
        pilot.behavior_key([bad], _attempt(99.9))


def test_target_step_limit_is_coverage_debt_not_a_semantic_failure():
    returning = "jr ra\nnop"
    long_finite = ("li t0,11000\nloop:\naddiu t0,t0,-1\n"
                   "bnez t0,loop\nnop\njr ra\nnop")
    passed = _named_result("fast", returning, returning)
    target_limited = _named_result("slow", long_finite, long_finite)

    assert target_limited.status == "inconclusive"
    assert target_limited.target.status == "step_limit"
    assert pilot._observed_semantics_clean([passed, target_limited])
    assert pilot.acceptance_key(
        [passed, target_limited], _attempt(99.0)) > pilot.acceptance_key(
        [passed, target_limited], _attempt(98.0))
    assert pilot.preserves_verified_prefix(
        [passed, target_limited], [passed, target_limited])


def test_target_limited_cases_are_omitted_from_exactness_prompt_trace():
    returning = "jr ra\nnop"
    long_finite = ("li t0,11000\nloop:\naddiu t0,t0,-1\n"
                   "bnez t0,loop\nnop\njr ra\nnop")
    passed = _named_result("fast", returning, returning)
    target_limited = _named_result("slow", long_finite, long_finite)
    attempt = Attempt(True, 98.333, False,
                      "--- target\n+++ candidate\n-addiu t5,a2,2\n"
                      "+addiu t3,a2,2", "", "")

    prompt = pilot.build_diagnosis_prompt(
        returning, returning, "void f(void) {}", attempt,
        [passed, target_limited], [])
    ledger = pilot.next_bad_observable_ledger(
        [passed, target_limited], "void f(void) {}")
    summary = pilot._differential_summary([target_limited])
    cases = (differential.TestCase("fast", 1),
             differential.TestCase("slow", 1))
    selected = pilot._diagnostic_cases(cases, [passed, target_limited])

    assert "BYTE EXACTNESS WITH INCONCLUSIVE TARGET COVERAGE" in prompt
    assert "LOGIC FIRST" not in prompt
    assert "not evidence of a candidate semantic bug" in ledger
    assert "Physical MIPS register names are allocation results" in ledger
    assert [case.name for case in selected] == ["fast"]
    recorded = summary["results"][0]
    assert recorded["trace_omitted"] == \
        "target did not reach observable completion"
    assert "trace" not in recorded["target"]


def test_exactness_prompt_does_not_ask_for_a_behavioral_divergence():
    returning = "jr ra\nnop"
    long_finite = ("li t0,11000\nloop:\naddiu t0,t0,-1\n"
                   "bnez t0,loop\nnop\njr ra\nnop")
    passed = _named_result("fast", returning, returning)
    target_limited = _named_result("slow", long_finite, long_finite)
    attempt = Attempt(
        True, 98.333, False,
        "--- target\n+++ candidate\n-addiu t5,a2,2\n+addiu t3,a2,2\n"
        "-sh t9,-2(t5)\n+sh a0,-2(t3)", "", "")

    prompt = pilot.build_diagnosis_prompt(
        returning, returning,
        "void f(u16 *dst) { u16 *t5; t5 = dst + 1; }",
        attempt, [passed, target_limited], [])

    assert "There is NO observed behavioral divergence to diagnose" in prompt
    assert "identify the source-level cause of the next behavioral divergence" \
        not in prompt
    assert "REGISTER-OPERAND-ONLY" in prompt
    assert "A C local named" in prompt
    assert "does not request physical register t5" in prompt
    assert "Register correspondences (target -> candidate)" in prompt
    assert "Entry ABI parameter map" in prompt
    assert "a0 = C parameter `dst`" in prompt
    assert "raw residual withheld" in prompt
    assert "1. RESIDUAL CLASSIFICATION" in prompt


def test_exactness_shape_guard_rejects_value_and_condition_changes():
    target = "jr ra\nnop"
    passed = _result(target, target)
    diff = ("--- target\n+++ candidate\n-addiu t5,a2,2\n"
            "+addiu t3,a2,2\n-sh t9,-2(t5)\n+sh a0,-2(t3)")
    source = """\
void f(u16 *dst, s32 count) {
    u16 *t5;
    s32 x;
    s32 y;
    t5 = dst + 1;
    if (count <= 0) *t5 = 0;
}
"""

    with pytest.raises(ValueError, match="does not support changing.*literals"):
        pilot.validate_exactness_shape_edit(
            source, source.replace("dst + 1", "dst + 2"), [passed], diff)
    with pytest.raises(ValueError, match="no evidence for changing an if"):
        pilot.validate_exactness_shape_edit(
            source, source.replace("count <= 0", "count == 0"),
            [passed], diff)


def test_exactness_shape_guard_rejects_incomplete_epoch_splits():
    assembly = "jr ra\nnop"
    passed = _result(assembly, assembly)
    diff = ("--- target\n+++ candidate\n-addiu t5,a2,2\n"
            "+addiu t3,a2,2\n-sh t9,-2(t5)\n+sh a0,-2(t3)")
    source = """\
void f(void) {
    s32 a3;
    a3 = 1;
    use(a3);
}
"""

    renamed_declaration_only = source.replace("s32 a3;", "s32 a3_epoch1;")
    with pytest.raises(ValueError, match="declaration.*a3.*tokens remain"):
        pilot.validate_exactness_shape_edit(
            source, renamed_declaration_only, [passed], diff)

    unused_new_local = source.replace("s32 a3;", "s32 a3;\n    s32 later;")
    with pytest.raises(
            ValueError, match="newly declared local `later` has no value use"):
        pilot.validate_exactness_shape_edit(
            source, unused_new_local, [passed], diff)

    definition_without_use = source.replace(
        "s32 a3;", "s32 a3;\n    s32 later;").replace(
            "a3 = 1;", "a3 = 1;\n    later = a3;")
    with pytest.raises(
            ValueError, match="newly declared local `later` has no value use"):
        pilot.validate_exactness_shape_edit(
            source, definition_without_use, [passed], diff)

    duplicate_after_normalization = source.replace(
        "a3 = 1;", "s32 a3 = 1;")
    duplicate_after_normalization = pilot.c89.to_c89(
        duplicate_after_normalization)
    with pytest.raises(ValueError, match="`a3` is declared multiple times"):
        pilot.validate_exactness_shape_edit(
            source, duplicate_after_normalization, [passed], diff)

    reordered = source.replace("    s32 x;\n    s32 y;",
                               "    s32 y;\n    s32 x;")
    pilot.validate_exactness_shape_edit(source, reordered, [passed], diff)


def test_compact_diagnosis_accepts_exactness_conclusions():
    diagnosis = """\
analysis before conclusions
1. RESIDUAL CLASSIFICATION: register operands only
2. TARGET REGISTER/INSTRUCTION ROLES: t5 is the output cursor
3. CANDIDATE C LIVE RANGES AND SOURCE ORDER: cursor overlaps counter
4. SEMANTICS-PRESERVING SOURCE-SHAPE CAUSE: initializer order changes overlap
5. MINIMAL PATCH PLAN: move `cursor = dst + 1;` after `count = 0;`
"""

    compact = pilot._compact_diagnosis(diagnosis)

    assert compact.startswith("1. RESIDUAL CLASSIFICATION")
    assert "5. MINIMAL PATCH PLAN" in compact


def test_next_bad_observable_ledger_locks_exact_store_operands():
    target = """
        li t0,0x59
        sh t0,4(a0)
        jr ra
        nop
    """
    candidate = target.replace("li t0,0x59", "li t0,0x401")
    result = _result(target, candidate)
    source = """\
void f(u16 *p) {
    if (flag == 0) p[2] = literal;
    else p[2] = (length << 10) | offset;
}
"""

    ledger = pilot.next_bad_observable_ledger([result], source)

    assert "target opcode: sh t0,4(a0)" in ledger
    assert "candidate opcode: sh t0,4(a0)" in ledger
    assert "ADDRESS EXACT" in ledger
    assert "WIDTH EXACT" in ledger
    assert "VALUE DIFFERS: target 0x59; candidate 0x401" in ledger
    assert "LOCKED OPERANDS: address, width" in ledger
    assert "Do not edit their C pointer/type machinery" in ledger
    assert "do not hard-code one case" in ledger
    assert "candidate high field `length` is target +1" in ledger
    assert "C line 2: if (flag == 0) p[2] = literal;" in ledger


def test_next_bad_observable_ledger_excludes_unexecuted_literal_edit():
    target = """
        lbu t0,0(a0)
        sh t0,4(a0)
        jr ra
        nop
    """
    candidate = """
        li t1,1
        sll t1,t1,10
        li t2,1
        or t0,t1,t2
        sh t0,4(a0)
        jr ra
        nop
    """
    source = """\
void f(u16 *p) {
    if (length == 0) p[2] = literal;
    else p[2] = (length << 10) | offset;
}
"""

    ledger = pilot.next_bad_observable_ledger(
        [_result(target, candidate)], source)

    assert "Editing that literal load cannot change this executed bad value" \
        in ledger


def test_next_bad_observable_ledger_shows_comparison_counter_order():
    target = """
        lbu t0,0(a0)
        lbu t1,1(a0)
        addiu t2,t2,1
        bne t0,t1,done
        nop
        addiu t3,t3,1
    done:
        sh t2,4(a0)
        jr ra
        nop
    """
    candidate = target.replace("sh t2,4(a0)", "sh t3,4(a0)")
    ledger = pilot.next_bad_observable_ledger(
        [_result(target, candidate)], "void f(u16 *p) { p[2] = count; }")

    assert "TARGET EXECUTED BYTE-COMPARISON WINDOW" in ledger
    assert "bne t0,t1,done" in ledger
    assert "increment executed before the byte inequality" in ledger


def test_counter_role_bridge_maps_target_success_counter_to_current_c():
    target = """
        move a3,zero
        lbu t0,0(a0)
        lbu t2,0(a0)
        addiu t3,t3,1
        bne t0,t2,done
        nop
        addiu a3,a3,1
        move t1,a3
    done:
        sll t6,t1,10
        sh t6,4(a0)
        jr ra
        nop
    """
    candidate = target.replace("sh t6,4(a0)", "sh zero,4(a0)")
    source = """\
void f(u8 *left, u8 *right, u16 *out) {
    if (a2 < 0) break;
    t3++;
    if (*left != *right) break;
    left++; right++; a0--;
    if (t3 == a0) break;
    if (t1 < t3) { t0 = t2; t1 = t3; }
    *out = (t1 << 10) | t0;
}
"""

    bridge = "\n".join(pilot._counter_role_bridge(
        [_result(target, candidate)], source))

    assert "`t3` before testing byte inequality" in bridge
    assert "`a3` is the successful-byte count" in bridge
    assert "compare/assign `t1` from `a3`" in bridge
    assert "replace the limit mutation with `a3++;`" in bridge


def test_fixed_compare_limit_bridge_maps_candidate_decrement_to_c_line():
    target = """
        addiu t3,t3,1
        bne t3,t4,loop
        nop
        sh t3,4(a0)
        jr ra
        nop
    loop:
        sh t3,4(a0)
        jr ra
        nop
    """
    candidate = """
        addiu t3,t3,1
        addiu t4,t4,-1
        bne t3,t4,loop
        nop
        sh t3,4(a0)
        jr ra
        nop
    loop:
        sh t3,4(a0)
        jr ra
        nop
    """
    source = """\
void f(void) {
    p++; q++; a3++; t4--;
    if (t3 == t4) break;
}
"""

    bridge = "\n".join(pilot._fixed_compare_limit_bridge(
        [_result(target, candidate)], source))

    assert "Target executes `bne t3,t4,loop`" in bridge
    assert "Candidate executes `addiu t4,t4,-1`" in bridge
    assert "with `p++; q++; a3++;`" in bridge


def test_observable_lock_rejects_pointer_edit_but_allows_value_control_edit():
    target = "li t0,0x59\nsh t0,4(a0)\njr ra\nnop"
    candidate = "li t0,0x401\nsh t0,4(a0)\njr ra\nnop"
    result = _result(target, candidate)
    source = """\
void f(u16 *p) {
    if (length == 0) p[2] = literal;
    else p[2] = (length << 10) | offset;
}
"""

    with pytest.raises(ValueError, match="ADDRESS and WIDTH are already EXACT"):
        pilot.validate_observable_locks(
            source, source.replace("p[2] = literal", "p[1] = literal"),
            [result])

    pilot.validate_observable_locks(
        source, source.replace("length == 0", "matches == 0"), [result])


def test_acceptance_key_ignores_static_shape_while_same_failure_remains():
    target = "sw zero,0(a0)\njr ra\nnop"
    failed = _result(target, "sw zero,4(a0)\njr ra\nnop")

    assert pilot.acceptance_key([failed], _attempt(1.0)) == \
        pilot.acceptance_key([failed], _attempt(99.9))


def test_acceptance_key_rewards_verified_write_address_progress():
    target = "addiu t0,a0,2\nsh zero,0(t0)\njr ra\nnop"
    far = "addiu t0,a0,8\nsh zero,0(t0)\njr ra\nnop"
    near = "addiu t0,a0,6\nsh zero,0(t0)\njr ra\nnop"
    case = differential.TestCase("case", 1)
    far_result = differential.run_suite(target, far, (case,))[0]
    near_result = differential.run_suite(target, near, (case,))[0]

    assert pilot._different_bytes(far_result) == \
        pilot._different_bytes(near_result)
    assert pilot._write_observable_distance(near_result) < \
        pilot._write_observable_distance(far_result)
    assert pilot.acceptance_key([near_result], _attempt()) > \
        pilot.acceptance_key([far_result], _attempt())


def test_acceptance_key_keeps_exact_next_write_operand_ahead_of_global_noise():
    target = """
        sh zero,0(a0)
        li t0,1
        sh t0,4(a0)
        jr ra
        nop
    """
    wrong_address = target.replace("sh t0,4(a0)", "sh t0,2(a0)")
    exact_address_wrong_value = target.replace("li t0,1", "li t0,2")
    case = differential.TestCase("case", 1)
    current = differential.run_suite(
        target, wrong_address, (case,))[0]
    child = differential.run_suite(
        target, exact_address_wrong_value, (case,))[0]

    # Simulate a temporarily worse aggregate state after exposing later bytes.
    for index in range(10):
        child.candidate.persistent_state[f"diagnostic-noise-{index}"] = index

    assert pilot._next_write_observable_progress(child) > \
        pilot._next_write_observable_progress(current)
    assert pilot._different_bytes(child) > pilot._different_bytes(current)
    assert pilot.acceptance_key([child], _attempt()) > \
        pilot.acceptance_key([current], _attempt())


def test_acceptance_key_does_not_reward_downstream_state_without_frontier_move():
    target = "li t0,1\nsh t0,4(a0)\njr ra\nnop"
    candidate = "li t0,2\nsh t0,4(a0)\njr ra\nnop"
    current = _result(target, candidate)
    downstream_only = _result(target, candidate)
    current.target.persistent_state["downstream"] = 0
    downstream_only.target.persistent_state["downstream"] = 0
    current.candidate.persistent_state["downstream"] = 1
    downstream_only.candidate.persistent_state["downstream"] = 0

    assert pilot._different_bytes(current) > \
        pilot._different_bytes(downstream_only)
    assert pilot.acceptance_key([current], _attempt()) == \
        pilot.acceptance_key([downstream_only], _attempt())


def test_behavior_key_uses_call_argument_prefix_before_byte_score():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        jal observe
        move a0,a0
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    bad = target.replace("move a0,a0", "addiu a0,a0,4")
    case = differential.TestCase("case", 1)
    good_result = differential.run_suite(
        target, target, (case,), call_arities={"observe": 1})[0]
    bad_result = differential.run_suite(
        target, bad, (case,), call_arities={"observe": 1})[0]
    assert pilot.behavior_key([good_result], _attempt(1.0)) > \
        pilot.behavior_key([bad_result], _attempt(99.9))


def test_prefix_guard_rejects_regression_and_accepts_forward_progress():
    target = """
        addiu sp,sp,-8
        sw ra,4(sp)
        jal observe
        move a0,a0
        lw ra,4(sp)
        jr ra
        addiu sp,sp,8
    """
    bad = target.replace("move a0,a0", "addiu a0,a0,4")
    case = differential.TestCase("case", 1)
    exact_result = differential.run_suite(
        target, target, (case,), call_arities={"observe": 1})[0]
    bad_result = differential.run_suite(
        target, bad, (case,), call_arities={"observe": 1})[0]

    assert pilot.preserves_verified_prefix([bad_result], [exact_result])
    assert not pilot.preserves_verified_prefix([exact_result], [bad_result])


def test_prefix_guard_rejects_returned_candidate_becoming_nonterminating():
    target = """
        sw zero,0(a0)
        sw zero,4(a0)
        jr ra
        nop
    """
    current = target.replace("sw zero,4(a0)", "li t0,1\nsw t0,4(a0)")
    child = """
        sw zero,0(a0)
        sw zero,4(a0)
    loop:
        b loop
        nop
    """
    case = differential.TestCase("case", 1)
    current_result = differential.run_suite(target, current, (case,))[0]
    child_result = differential.run_suite(target, child, (case,),
                                           max_steps=50)[0]

    assert current_result.candidate.status == "returned"
    assert child_result.candidate.status == "nonreturn"
    assert pilot.behavior_key([child_result], _attempt()) > \
        pilot.behavior_key([current_result], _attempt())
    assert not pilot.preserves_verified_prefix(
        [current_result], [child_result])


def test_final_memory_improvement_beats_diagnostic_write_order():
    target = """
        sw zero,0(a0)
        sw zero,4(a0)
        sw zero,8(a0)
        jr ra
        nop
    """
    current = """
        sw zero,0(a0)
        li t0,1
        sw t0,4(a0)
        sw t0,8(a0)
        jr ra
        nop
    """
    child = """
        sw zero,8(a0)
        sw zero,0(a0)
        li t0,1
        sw t0,4(a0)
        jr ra
        nop
    """
    case = differential.TestCase("case", 1)
    current_result = differential.run_suite(target, current, (case,))[0]
    child_result = differential.run_suite(target, child, (case,))[0]

    assert pilot._write_prefix(current_result) > pilot._write_prefix(child_result)
    assert pilot._different_bytes(child_result) < \
        pilot._different_bytes(current_result)
    assert pilot.preserves_verified_prefix([current_result], [child_result])
    assert pilot.behavior_key([child_result], _attempt()) > \
        pilot.behavior_key([current_result], _attempt())
