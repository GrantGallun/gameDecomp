"""Re-certification of integration nodes whose pinned build inputs changed (eval.campaign_integration)."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

from eval import campaign_integration as ci


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _world(tmp_path: Path, status="integrated"):
    src = tmp_path / "f.c"
    src.write_text("void f(void) {}\n")
    build = tmp_path / "build.sh"
    build.write_text("old helper\n")
    cert = {"build_inputs": {str(build): _sha(b"old helper\n")}, "exact": False,
            "function_boundary": {"function_exact": True}}
    node = {"status": status, "source": str(src), "source_sha256": _sha(src.read_bytes()),
            "attempt_id": 1, "verification": cert}
    state = {"nodes": {"f": node}, "pins": {}, "integration_sweep": {
        "schema_version": 1, "policy": ci.POLICY, "attempted": {}, "verified_union": ["f"],
        "verified_source_bindings": {"f": ci.source_binding(node)}}}
    build.write_text("new helper\n")               # maintenance replaced a pinned input
    return state, build


def _good(build):
    def score(repo, db, name, source, parent, run_id):
        return SimpleNamespace(compiled=True, exact=False, receipt_id=99, frontend={"passed": True},
                               verification={"build_inputs": {str(build): _sha(build.read_bytes())},
                                             "function_boundary": {"function_exact": True}})
    return score


def test_stale_inputs_names_the_changed_file(tmp_path):
    state, build = _world(tmp_path)
    assert ci.stale_inputs(state["nodes"]["f"]) == [str(build)]


def test_recertify_renews_certificate_and_verified_binding(tmp_path):
    state, build = _world(tmp_path)
    records = ci.recertify(state, repo=tmp_path, db=None, score=_good(build))
    node = state["nodes"]["f"]
    assert [r["result"] for r in records] == ["recertified"]
    assert node["attempt_id"] == 99 and ci.stale_inputs(node) == []
    assert state["integration_sweep"]["verified_source_bindings"]["f"] == ci.source_binding(node)
    assert state["integration_sweep"]["recertifications"][-1]["records"][0]["old_binding"]["attempt_id"] == 1


def test_recertify_refuses_when_no_longer_function_exact(tmp_path):
    state, build = _world(tmp_path)
    before = dict(state["nodes"]["f"])

    def bad(*a):
        return SimpleNamespace(compiled=True, exact=False, receipt_id=5, frontend={"passed": True},
                               verification={"function_boundary": {"function_exact": False}})
    records = ci.recertify(state, repo=tmp_path, db=None, score=bad)
    assert records[0]["result"] == "failed" and "do not re-certify" in records[0]["error"]
    assert state["nodes"]["f"] == before            # nothing changed; the union check still halts, visibly


def test_recertify_refuses_a_changed_source(tmp_path):
    state, build = _world(tmp_path)
    Path(state["nodes"]["f"]["source"]).write_text("void f(void) { return; }\n")
    records = ci.recertify(state, repo=tmp_path, db=None, score=_good(build))
    assert records[0]["result"] == "failed" and "no longer matches" in records[0]["error"]


def test_sweep_reports_renewed_nodes_even_with_nothing_selected(tmp_path, monkeypatch):
    # The motivating state (2026-09-29): every pending node already marked attempted, so the old sweep
    # returned [] before doing anything, and a stale member kept halting it forever. The renewal must be
    # returned as changed, or the checkpoint store (which rewrites only changed nodes) would lose it.
    state, build = _world(tmp_path)
    real = ci.recertify
    monkeypatch.setattr(ci, "recertify", lambda s, repo, db: real(s, repo=repo, db=db, score=_good(build)))
    changed = ci.sweep(state, repo=tmp_path, db=None, artifacts=tmp_path / "art")
    assert changed == ["f"]
