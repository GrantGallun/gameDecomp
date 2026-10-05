"""Motivating binary streams must produce candidates through the learner's API."""
from pathlib import Path

import pytest

from eval import tool_agent, tool_registry
from solver import wide_runtime_interfaces as wide

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("fixture,signature,body", [
    ("wide_divremi.s", "void arbitrary(u64 *quotient, u64 *remainder, u64 value, u16 divisor)",
     "*quotient = value / divisor;"),
    ("wide_modulo.s", "s64 arbitrary(s64 dividend, s64 divisor)", "remainder += divisor;"),
])
def test_complete_binary_shape_reconstructs_operation_and_abi(fixture, signature, body):
    source = '#include "common.h"\nu64 arbitrary(u64 broken, s32 extra) { return broken >> 32; }\nint untouched(void) { return 7; }\n'
    result, report = wide.reconstruct_helper(source, "arbitrary", (FIXTURES / fixture).read_text(),
        big_endian_o32=True, compiler_mips="-mips3 -32")
    assert signature in result and body in result
    assert "extra" not in result and "broken" not in result
    assert result.startswith('#include "common.h"\n')
    assert result.endswith('int untouched(void) { return 7; }\n')
    assert report["changes"] and report["assembly_sha256"]


@pytest.mark.parametrize("fixture,old,new", [
    ("wide_divremi.s", "0x12($sp)", "0x16($sp)"),
    ("wide_divremi.s", "mfhi", "mflo"),
    ("wide_divremi.s", "ddivu", "ddiv"),
    ("wide_modulo.s", "bgtz", "bgez"),
    ("wide_modulo.s", "daddu", "dsubu"),
    ("wide_modulo.s", "break      6", "nop"),
])
def test_changed_memory_arithmetic_condition_or_trap_is_not_the_same_recipe(fixture, old, new):
    asm = (FIXTURES / fixture).read_text()
    assert old in asm
    source = "s64 f(void) { return 0; }"
    result, report = wide.reconstruct_helper(source, "f", asm.replace(old, new),
        big_endian_o32=True, compiler_mips="-mips3 -32")
    assert result == source and not report["changes"]


@pytest.mark.parametrize("fixture", ["wide_divremi.s", "wide_modulo.s"])
def test_new_reconstruction_does_not_expand_callee_interface_or_bypass_abi_gates(fixture):
    asm = (FIXTURES / fixture).read_text()
    assert wide.recognize(asm) is None
    source = "s64 f(void) { return 0; }"
    for endian, flags in [(False, "-mips3 -32"), (True, "-mips2")]:
        assert wide.reconstruct_helper(source, "f", asm,
            big_endian_o32=endian, compiler_mips=flags)[0] == source
    assert wide.reconstruct_helper(source, "f", asm + "\nsw zero,0(a0)\n",
        big_endian_o32=True, compiler_mips="-mips3 -32")[0] == source


def runner_context(tmp_path):
    # Minimal real ELF header for the existing ABI reader; compile remains external.
    header = bytearray(52)
    header[:6] = b"\x7fELF\x01\x02"
    header[18:20] = (8).to_bytes(2, "big")
    header[36:40] = (0x1000).to_bytes(4, "big")
    (tmp_path / "target.o").write_bytes(header)
    (tmp_path / "target.s").write_text((FIXTURES / "wide_divremi.s").read_text())
    return {"function": "f", "candidate": "void f(void) {}", "workspace": str(tmp_path),
            "target_asm_path": str(tmp_path / "target.s"),
            "initial_verdict": {"compiled": True, "exact": False, "score": 79.37,
                "compiler_recipe": {"settings": {"C_MIPS": "-mips3 -32"}}}}


def test_registered_repair_runs_on_compiling_nonexact_candidate_without_claiming_exact(tmp_path):
    action = tool_registry.ACTIONS["reconstruct-wide"]
    context = runner_context(tmp_path)
    result = action.resolve()(context, {})
    assert result["changed"] and "*remainder = value % divisor;" in result["source"]
    assert result["exact"] is False
    assert context["candidate"] == "void f(void) {}"


def test_missing_recipe_and_wrong_target_path_decline_with_reasons(tmp_path):
    action = tool_registry.ACTIONS["reconstruct-wide"]
    context = runner_context(tmp_path)
    context["initial_verdict"]["compiler_recipe"] = {}
    result = action.resolve()(context, {})
    assert not result["changed"] and result["reason"]
    context = runner_context(tmp_path)
    context["target_asm_path"] = str(tmp_path / "unbound.s")
    result = action.resolve()(context, {})
    assert not result["changed"] and result["reason"]


def test_default_scripted_policy_can_select_the_new_repair(tmp_path):
    context = tool_agent.Context(**runner_context(tmp_path))
    # Feed the verified starting observation through the real controller.
    transcript = tool_agent.run_episode(context, tool_agent.ScriptedPolicy(), budget=1,
        runners={"eval.tool_runners.closed_wide_runtime": lambda c, p: {"status": "no-change", "changed": False, "exact": False}})
    assert transcript.steps[-1].action == "reconstruct-wide"


def test_scripted_policy_does_not_spend_a_slot_on_wrong_compiler_isa(tmp_path):
    context = tool_agent.Context(**runner_context(tmp_path))
    context.initial_verdict["compiler_recipe"]["settings"]["C_MIPS"] = "-mips2"
    transcript = tool_agent.run_episode(context, tool_agent.ScriptedPolicy(), budget=1)
    assert transcript.steps[-1].action != "reconstruct-wide"
