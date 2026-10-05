"""Campaign wiring of trace-guided register search: profile pin -> agentrepair -> regalloc_search(trace=...)."""
import hashlib
import inspect

from eval import agentrepair
from eval import completion_campaign as campaign


def test_profile_pins_the_tracing_toolchain_and_passes_it_through():
    profile = next(p for p in campaign.PROFILES if p["name"] == "regalloc_search")
    assert profile["regalloc_budget"] == 300 and not profile["model"]
    assert profile["regalloc_trace_cc"].endswith("ido-trace/cc") and len(profile["regalloc_trace_uopt_sha256"]) == 64
    source = inspect.getsource(campaign.execute)
    assert 'regalloc_trace_cc=profile.get("regalloc_trace_cc")' in source
    assert 'regalloc_trace_uopt_sha256=profile.get("regalloc_trace_uopt_sha256")' in source
    parameters = inspect.signature(agentrepair.run).parameters
    assert "regalloc_trace_cc" in parameters and "regalloc_trace_uopt_sha256" in parameters


def test_trace_is_enabled_only_when_the_pinned_uopt_hash_matches(tmp_path):
    cc = tmp_path / "cc"
    cc.write_text("")
    (tmp_path / "uopt").write_bytes(b"patched uopt")
    good = hashlib.sha256(b"patched uopt").hexdigest()
    assert agentrepair._regalloc_trace(tmp_path, tmp_path, "f", "", str(cc), good) is not None
    assert agentrepair._regalloc_trace(tmp_path, tmp_path, "f", "", str(cc), "0" * 64) is None
    assert agentrepair._regalloc_trace(tmp_path, tmp_path, "f", "", None, good) is None
    assert agentrepair._regalloc_trace(tmp_path, tmp_path, "f", "", str(tmp_path / "missing" / "cc"), good) is None


def test_trace_callback_declines_uncompiled_candidates(tmp_path):
    from solver import regalloc_search
    cc = tmp_path / "cc"
    cc.write_text("")
    (tmp_path / "uopt").write_bytes(b"x")
    trace = agentrepair._regalloc_trace(tmp_path, tmp_path, "f", "", str(cc), hashlib.sha256(b"x").hexdigest())
    assert trace("void f(void) {}", "label", regalloc_search.Compiled(False, False, None)) is None
