"""The recorded-call panel puts real-game failures in front of the model."""
from types import SimpleNamespace

from eval import trace_panel
from solver import trace_replay, workspace

import test_trace_replay as fixture

REPLAY = trace_replay.replay


class Base:
    report = {"kind": "base"}

    def __init__(self, result):
        self.result, self.calls = result, 0

    def __call__(self, state):
        self.calls += 1
        return dict(self.result)


def panel(tmp_path, monkeypatch, base, recordings):
    obj = tmp_path / "cand.o"
    obj.write_bytes(b"")
    (tmp_path / "cand_object_dump_normalized.s").write_text("candidate")
    monkeypatch.setattr(workspace, "semantic_assembly", lambda text, _obj: text)
    built = trace_panel.Panel(base, tmp_path, tmp_path, "probe", recordings)
    built._context = (fixture.ORIGINAL, {"callee": 2}, ("v0",), {"callee": fixture.CALLEE}, fixture.SIZE)
    return built, SimpleNamespace(source="int x;", object_path=obj)


SYNTHETIC = {"status": "passed_with_execution_debt", "semantic_key": [1, 2],
             "feedback": [{"source": "synthetic"}], "counts": {"passed": 3}}


def test_recorded_failure_becomes_primary_counterexample(tmp_path, monkeypatch):
    wrong = fixture.ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)")
    monkeypatch.setattr(trace_panel.trace_replay, "replay",
                        lambda rec, target, cand, **kw: REPLAY(rec, target, wrong, **kw))
    built, state = panel(tmp_path, monkeypatch, Base(SYNTHETIC), [fixture.record()])
    result = built(state)
    assert result["status"] == "observed_failure"
    assert result["feedback"][0]["source"] == "recorded game execution of the original ROM"
    assert "a0+0x8" in result["feedback"][0]["difference"]
    assert result["feedback"][1] == {"source": "synthetic"}
    assert result["semantic_key"][:3] == [-1, -1, 0]
    assert result["counts"]["recorded_distance"] == 1
    assert result["counts"]["recorded_failed"] == 1
    assert result["unrecorded_panel_status"] == "passed_with_execution_debt"


def test_pointer_parameters_map_registers_and_stop_at_floats():
    source = "struct A { int x; };\nvoid f(struct A *arg0, s32 n, Foo *p) {\n}\n"
    assert trace_panel.pointer_parameters(source, "f") == {"a0": ("arg0", "struct A"), "a2": ("p", "Foo")}
    floats = "void f(f32 x, Foo *p) {\n}\n"
    assert trace_panel.pointer_parameters(floats, "f") == {}


def test_failure_measures_field_names_once_and_reports_unavailability(tmp_path, monkeypatch):
    wrong = fixture.ORIGINAL.replace("sw t0,8(a0)", "sw t0,0xc(a0)")
    monkeypatch.setattr(trace_panel.trace_replay, "replay",
                        lambda rec, target, cand, **kw: REPLAY(rec, target, wrong, **kw))
    calls = []

    def measured(repo, ws, source, function, target):
        calls.append(source)
        return {"a0": {"parameter": "arg0", "record": "struct A",
                       "rows": [{"member": "count", "offset": 8, "width": 4}]}}

    monkeypatch.setattr(trace_panel, "parameter_fields", measured)
    built, state = panel(tmp_path, monkeypatch, Base(SYNTHETIC), [fixture.record()])
    state.source = "struct A { int c; };\nvoid probe(struct A *arg0) {\n}\n"
    result = built(state)
    assert "(&arg0->count)" in result["feedback"][0]["difference"]
    built.cache.clear()
    state.source += "/* body-only change */"
    built(state)
    assert len(calls) == 1           # declarations unchanged: layout reused

    def broken(*args):
        raise ValueError("probe rejected")

    monkeypatch.setattr(trace_panel, "parameter_fields", broken)
    built, state = panel(tmp_path, monkeypatch, Base(SYNTHETIC), [fixture.record()])
    state.source = "void probe(struct B *arg0) {\n}\n"
    result = built(state)
    assert result["status"] == "observed_failure"
    assert "probe rejected" in result["recorded_field_names"]


def test_passing_recordings_add_debt_note_and_rank_above_none(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_panel.trace_replay, "replay",
                        lambda rec, target, cand, **kw: REPLAY(rec, target, target, **kw))
    built, state = panel(tmp_path, monkeypatch, Base(SYNTHETIC), [fixture.record()])
    result = built(state)
    assert result["counts"]["recorded_passed"] == 1
    assert result["feedback"] == [{"source": "synthetic"}]
    assert any("recorded game calls behave identically" in item for item in result["debt"])
    assert result["semantic_key"][:3] == [0, 0, 1]


def test_unusable_or_foreign_recordings_never_judge(tmp_path, monkeypatch):
    other = {**fixture.record(), "function": "someoneElse"}
    moved = {**fixture.record(), "end_address": fixture.ENTRY + fixture.SIZE + 16}
    base = Base(SYNTHETIC)
    built, state = panel(tmp_path, monkeypatch, base, [other, moved])
    assert built.recordings == [moved]
    result = built(state)
    assert result["status"] == "passed_with_execution_debt"
    assert result["counts"]["recorded_unusable"] == 1
    assert "extent" in result["recorded_call_results"][0]["reasons"][0]


def test_panel_caches_by_source(tmp_path, monkeypatch):
    base = Base(SYNTHETIC)
    built, state = panel(tmp_path, monkeypatch, base, [fixture.record()])
    first = built(state)
    assert built(state) is first


def test_alignment_padding_after_the_symbol_does_not_make_recordings_unusable(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_panel.trace_replay, "replay",
                        lambda rec, target, cand, **kw: REPLAY(rec, target, target, **kw))
    built, state = panel(tmp_path, monkeypatch, Base(SYNTHETIC), [fixture.record()])
    target, arities, returns, symbols, _size = built._context
    padding = "    nop" + chr(10) + "    nop" + chr(10)
    built._context = (target + padding, arities, returns, symbols, fixture.SIZE + 8)
    result = built(state)
    assert result["counts"]["recorded_passed"] == 1, result["recorded_call_results"]
