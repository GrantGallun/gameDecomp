"""Register-search compiler receipts and source-bound parent evidence."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from eval import agentrepair
from solver import regalloc_search, workspace


def test_agentrepair_regalloc_attempts_are_durable_and_frontend_gated(tmp_path, monkeypatch):
    db = tmp_path / "worker.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).resolve().parents[1] / "kb/schema.sql").read_text())
        conn.execute("INSERT INTO tus(id,name) VALUES(1,'unit.c')")
        conn.execute("INSERT INTO functions(addr,name,size,tu_id) VALUES(1,'f',12,1)")
        root = workspace.Attempt(True, 90, False, "root diff", "", "")
        workspace.record_attempt(conn, "f", "ROOT", root, strategy="root")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "target_object_dump_normalized.s").write_text("TARGET")
        observed = []

        def score(_ws, _repo, tag, code, **kwargs):
            att = workspace.Attempt(code != "BAD", 100 if code in {"GOOD", "PASS"} else 0,
                                    code in {"GOOD", "PASS"}, "diff",
                                    "failed" if code == "BAD" else "", "")
            att.frontend = {"passed": False if code == "GOOD" else True}
            att.source_attribution = {"candidate_source_sha256": code}
            att.compiler_recipe = {"target": "unit"}
            workspace.record_attempt(kwargs.pop("conn"), kwargs.pop("func"), code, att, **kwargs)
            (ws / f"{tag}_object_dump_normalized.s").write_text(code)
            return att

        def search(function, source, compile_candidate, target, **kwargs):
            callback = kwargs["compile_with_parent"]
            baseline = callback("ROOT", "baseline", None)
            failed = callback("BAD", "bad", "ROOT")
            gated = callback("GOOD", "good", "BAD")
            accepted = callback("PASS", "pass", "GOOD")
            observed.extend((baseline, failed, gated, accepted))
            return SimpleNamespace(summary=lambda: {}, improved=True, best_source="PASS")

        monkeypatch.setattr(workspace, "score", score)
        monkeypatch.setattr(regalloc_search, "search", search)
        result = agentrepair._regalloc_search(
            tmp_path, conn, ws, "f", "ROOT", SimpleNamespace(faults={"register_allocation": 1}),
            3, run_id="regalloc-test", config={"regalloc_budget": 3}, root_attempt_id=root.receipt_id)
        assert result is not None
        assert [row.exact for row in observed] == [False, False, False, True]
        assert observed[2].evidence == {
            "compiled": True, "score": 100, "source_attribution": {"candidate_source_sha256": "GOOD"},
            "frontend": {"passed": False}, "compiler_recipe": {"target": "unit"}}
        rows = conn.execute("SELECT id,source_code,compiled,parent_attempt_id,run_id FROM attempts "
                            "WHERE strategy='regalloc-search-probe' ORDER BY id").fetchall()
        assert len(rows) == 4
        assert [row[1:3] for row in rows] == [("ROOT", 1), ("BAD", 0), ("GOOD", 1), ("PASS", 1)]
        assert [row[3] for row in rows] == [root.receipt_id, rows[0][0], rows[1][0], rows[2][0]]
        assert all(row[4] == "regalloc-test" for row in rows)
        assert result.best_attempt_id == rows[-1][0]


def test_search_parent_callback_tracks_depth_two_and_enabling_root(monkeypatch):
    children = {"base": [("first", "family", "child")],
                "child": [("second", "family", "grandchild")],
                "enabled": [("enabled-child", "family", "exact")]}

    class Report:
        def __init__(self, dump):
            self.gradient = {"base": (3,), "child": (2,), "grandchild": (1,),
                             "enabled": (3,), "exact": (0,)}.get(dump, (9,))
            self.signatures = dump

    monkeypatch.setattr(regalloc_search.regalloc_signature, "compare",
                        lambda target, dump: Report(dump))
    seen_evidence = {}

    def variants(source, function, diff="", prefer=(), **kwargs):
        seen_evidence[source] = kwargs.get("evidence")
        return iter(children.get(source, []))

    monkeypatch.setattr(regalloc_search.regalloc_mutations, "variants",
                        variants)
    monkeypatch.setattr(regalloc_search.regalloc_mutations, "enabling_variants",
                        lambda source, function: iter([("enable", "family", "enabled")])
                        if source == "base" else iter([]))
    calls = []

    def compile_with_parent(source, label, parent_source):
        calls.append((source, parent_source))
        return regalloc_search.Compiled(True, source == "exact", source,
                                        evidence={"source_attribution": {"source": source}})

    result = regalloc_search.search("f", "base", lambda source, label: None, "target",
                                    budget=10, depth=3, enable=True,
                                    compile_with_parent=compile_with_parent)
    assert result.exact
    assert ("child", "base") in calls
    assert ("grandchild", "child") in calls
    assert ("enabled", "base") in calls
    assert ("exact", "enabled") in calls
    assert seen_evidence["base"]["source_attribution"]["source"] == "base"
    assert seen_evidence["child"]["source_attribution"]["source"] == "child"


def test_campaign_register_search_resolves_by_optimizer_key_and_logs_what_it_skips(tmp_path, monkeypatch):
    # eval/results/regalloc-keyed-20260927: the campaign passes key= and every resolved candidate is logged
    # with the parent it came from and the compile it reused.
    from solver import ido_stages
    db = tmp_path / "worker.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).resolve().parents[1] / "kb/schema.sql").read_text())
        conn.execute("INSERT INTO tus(id,name) VALUES(1,'unit.c')")
        conn.execute("INSERT INTO functions(addr,name,size,tu_id) VALUES(1,'f',12,1)")
        root = workspace.Attempt(True, 90, False, "root diff", "", "")
        workspace.record_attempt(conn, "f", "ROOT", root, strategy="root")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "target_object_dump_normalized.s").write_text("TARGET")
        keys = []
        monkeypatch.setattr(ido_stages, "optimizer_key",
                            lambda repo, ws_, fn, src: keys.append((repo, ws_, fn)) or src.rstrip("'"))

        def score(_ws, _repo, tag, code, **kwargs):
            att = workspace.Attempt(True, 50, False, "diff", "", "")
            workspace.record_attempt(kwargs.pop("conn"), kwargs.pop("func"), code, att, **kwargs)
            (ws / f"{tag}_object_dump_normalized.s").write_text(code)
            return att
        seen = {}

        def search(function, source, compile_candidate, target, **kwargs):
            seen.update(kwargs)
            kwargs["compile_with_parent"]("ROOT", "baseline", None)
            child = kwargs["compile_with_parent"]("CHILD", "stmt_move:1->2", "ROOT")
            assert kwargs["key"]("CHILD'") == kwargs["key"]("CHILD")        # a respelling, same object
            kwargs["resolved"]("CHILD'", "cast:1", "CHILD", "CHILD")
            return SimpleNamespace(summary=lambda: {}, improved=False, best_source="ROOT")

        monkeypatch.setattr(workspace, "score", score)
        monkeypatch.setattr(regalloc_search, "search", search)
        agentrepair._regalloc_search(
            tmp_path, conn, ws, "f", "ROOT", SimpleNamespace(faults={"register_allocation": 1}),
            3, run_id="regalloc-test", config={}, root_attempt_id=root.receipt_id)
        assert callable(seen.get("key")) and callable(seen.get("resolved"))
        assert keys and keys[0] == (tmp_path, ws, "f")
        child_id = conn.execute("SELECT id FROM attempts WHERE source_code='CHILD'").fetchone()[0]
        row = conn.execute("SELECT status, kind, hypothesis, parent_attempt_id, edits, raw_response "
                           "FROM model_proposals").fetchone()
        assert row[:4] == ("duplicate", "optimizer-key:regalloc", "cast:1", child_id)
        assert f'"same_object_as_attempt": {child_id}' in row[4] and row[5] == "CHILD'"


def test_campaign_checks_reuse_by_object_certificate_and_audits(tmp_path, monkeypatch):
    from solver import byte_certificate
    db = tmp_path / "worker.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript((Path(__file__).resolve().parents[1] / "kb/schema.sql").read_text())
        conn.execute("INSERT INTO tus(id,name) VALUES(1,'unit.c')")
        conn.execute("INSERT INTO functions(addr,name,size,tu_id) VALUES(1,'f',12,1)")
        root = workspace.Attempt(True, 90, False, "root diff", "", "")
        workspace.record_attempt(conn, "f", "ROOT", root, strategy="root")
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "target_object_dump_normalized.s").write_text("TARGET")

        def score(_ws, _repo, tag, code, **kwargs):
            att = workspace.Attempt(True, 50, False, "diff", "", "")
            workspace.record_attempt(kwargs.pop("conn"), kwargs.pop("func"), code, att, **kwargs)
            (ws / f"{tag}_object_dump_normalized.s").write_text(code)
            (ws / f"{tag}.o").write_bytes(b"OBJ:" + code.encode())
            return att
        certified = []

        def certify(left, right, source):
            certified.append((left.read_bytes(), right.read_bytes()))
            return {"status": "object_sections_exact", "exact": left.read_bytes() == right.read_bytes()}
        seen = {}

        def search(function, source, compile_candidate, target, **kwargs):
            seen.update(kwargs)
            a = kwargs["compile_with_parent"]("ROOT", "baseline", None)
            b = kwargs["compile_with_parent"]("CHILD", "move", "ROOT")
            seen["verdicts"] = (kwargs["same_object"](a, a), kwargs["same_object"](a, b),
                                kwargs["same_object"](a, regalloc_search.Compiled(True, False, "x")))
            return SimpleNamespace(summary=lambda: {}, improved=False, best_source="ROOT")

        monkeypatch.setattr(workspace, "score", score)
        monkeypatch.setattr(byte_certificate, "certify", certify)
        monkeypatch.setattr(regalloc_search, "search", search)
        agentrepair._regalloc_search(
            tmp_path, conn, ws, "f", "ROOT", SimpleNamespace(faults={"register_allocation": 1}),
            3, run_id="regalloc-test", config={}, root_attempt_id=root.receipt_id)
        assert seen["audit_rate"] == 0.02 and isinstance(seen["audit_seed"], int)
        assert seen["coalesce"] is True, "the campaign's register search runs scalar coalescing"
        # same object -> True, different object -> False, missing object -> unknown (never agreement)
        assert seen["verdicts"] == (True, False, None)
        assert certified[0] == (b"OBJ:ROOT", b"OBJ:ROOT") and certified[1] == (b"OBJ:ROOT", b"OBJ:CHILD")
        assert not list(ws.glob("*_keycheck_*"))                     # temporary objects cleaned up
