"""Invariants of the source-repair dataset exporter.

Every filter has a case here that makes it FIRE, and ``test_every_filter_is_load_bearing``
additionally re-runs the export with that one filter disabled and asserts the exported
record set changes. A filter that can only decline would fail that test, which is the
failure mode this project keeps paying for.

No GPU, no network, no ROM: the fixtures are small SQLite databases built in ``tmp_path``
plus a tmp ``eval/sets`` directory and a tmp workspace holding ``target.s`` files.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from eval import repair_dataset as rd

TU_OK = "build/src/game/ok.o"


# --- fixture ------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    create table attempts (
        id integer primary key, func_addr integer, iteration integer, source_code text,
        prompt_context text, compiled integer, compiler_stderr text, score real,
        diff_summary text, strategy text, model text, sampling text, wall_ms integer,
        token_cost integer, created_at integer, raw_response text, extract_status text,
        done_reason text, exact integer, run_id text, parent_attempt_id integer,
        source_sha256 text, prompt_sha256 text);
    create table attempt_edges (
        parent_attempt_id integer, child_attempt_id integer, relation text, action text,
        feedback text, created_at integer);
    create table attempt_runs (
        id text primary key, kind text, model text, config text, started_at integer);
    create table functions (
        addr integer primary key, name text, tu_id integer, size integer, insn_count integer,
        is_leaf integer, state text, best_score real, attempts integer);
    create table tus (id integer primary key, name text, object_path text, start_addr integer,
        end_addr integer, compiler text, flags text);
    create table model_proposals (
        id integer primary key, run_id text, parent_attempt_id integer, child_attempt_id integer,
        prompt_context text, prompt_sha256 text, raw_response text, status text, kind text,
        hypothesis text, edits text, model text, sampling text, wall_ms integer,
        token_cost integer, created_at integer);
    """)


class Fixture:
    """One KB covering every filter's motivating case."""

    def __init__(self, root: Path):
        self.root = root
        self.db = root / "kb.sqlite"
        self.sets = root / "sets"
        self.workspace = root / "nonmatchings"
        self.sets.mkdir(parents=True)
        self.workspace.mkdir(parents=True)
        self.conn = sqlite3.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        _schema(self.conn)
        self._functions()
        self._attempts()
        self._sealed_sets()
        self._target_asm()
        self.conn.commit()

    # -- functions ---------------------------------------------------------
    def _functions(self) -> None:
        self.tus = {
            1: TU_OK, 2: "build/src/libmus/player.o", 3: "build/src/game/finished.o",
            4: "build/src/game/dup.o", 5: "build/src/game/cluster.o",
            6: "build/src/game/panel.o", 7: "build/src/game/dev.o",
            8: "build/src/game/heldout.o", 9: "build/src/game/legacy.o",
            10: "build/src/game/recon.o", 11: "build/src/game/nofb.o",
            12: "build/src/game/empty.o", 13: "build/src/game/xfunc.o",
            14: "build/src/race/other.o", 15: "build/src/game/det.o",
            16: "build/src/game/nc.o", 17: "build/src/game/ni.o",
            18: "build/src/game/prop.o", 19: "build/src/game/unv.o",
            20: "build/src/game/small.o", 21: "build/src/game/hist.o",
        }
        for tu_id, name in self.tus.items():
            self.conn.execute("insert into tus (id, name, object_path) values (?,?,?)",
                              (tu_id, name, name))
        names = {
            "f_ok": 1, "f_lib": 2, "f_finished": 3, "f_dup": 4, "f_cluster": 5,
            "f_panel": 6, "f_dev": 7, "f_held": 8, "f_legacy": 9, "f_recon": 10,
            "f_nofb": 11, "f_empty": 12, "f_xfunc": 13, "f_other": 14, "f_det": 15,
            "f_nc": 16, "f_ni": 17, "f_prop": 18, "f_unv": 19, "f_small": 20, "f_hist": 21,
        }
        self.addr = {}
        for index, (name, tu_id) in enumerate(sorted(names.items())):
            addr = 0x80001000 + index * 0x40
            self.addr[name] = addr
            self.conn.execute(
                "insert into functions (addr, name, tu_id, size, insn_count, is_leaf, state) "
                "values (?,?,?,?,?,?,?)", (addr, name, tu_id, 32, 8, 1, "asm"))

    # -- attempts ----------------------------------------------------------
    def att(self, ident: int, func: str, score: float, *, compiled: int = 1, exact: int = 0,
            source: str | None = None, strategy: str = "", model: str = "",
            prompt: str = "", raw: str = "", diff: str = "d", sha: str | None = None,
            created: int = 1000, parent: int | None = None) -> int:
        self.conn.execute(
            "insert into attempts (id, func_addr, iteration, source_code, prompt_context, "
            "compiled, compiler_stderr, score, diff_summary, strategy, model, sampling, "
            "created_at, raw_response, exact, run_id, parent_attempt_id, source_sha256) "
            "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, self.addr[func], 0, source if source is not None else f"void {func}(void){{}}",
             prompt, compiled, "", score, diff, strategy, model, "", created, raw, exact,
             None, parent, sha))
        return ident

    def edge(self, parent: int, child: int, *, relation: str = "model-repair",
             action: str = "edit", feedback: str = "feedback") -> None:
        self.conn.execute(
            "insert into attempt_edges (parent_attempt_id, child_attempt_id, relation, action, "
            "feedback, created_at) values (?,?,?,?,?,?)",
            (parent, child, relation, action, feedback, 1000))

    def _attempts(self) -> None:
        # observed-repair with real model records
        self.att(560, "f_ok", 90.0, source="void f_ok(void){int a=1;}", diff="from ok parent")
        self.att(561, "f_ok", 95.0, source="void f_ok(void){int a=2;}", strategy="modelrepair-d1",
                 model="gpt-oss:20b", prompt="PROMPT", raw="RAW", diff="to ok child")
        self.edge(560, 561)
        # deterministic-repair, on the model-free registry
        self.att(570, "f_det", 80.0, source="void f_det(void){int a=1;}")
        self.att(571, "f_det", 88.0, source="void f_det(void){int a=2;}", strategy="alloc-order")
        self.edge(570, 571, relation="statement-order", action="move statement")
        # observed-repair whose only model evidence is a model_proposals row
        self.att(650, "f_prop", 70.0, source="void f_prop(void){int a=1;}")
        self.att(651, "f_prop", 77.0, source="void f_prop(void){int a=2;}",
                 strategy="differential-debugger-causal-repair")
        self.edge(650, 651)
        self.conn.execute(
            "insert into model_proposals (id, child_attempt_id, status, kind, model) "
            "values (?,?,?,?,?)", (1, 651, "valid", "differential-debugger-repair",
                                   "gpt-oss:20b"))
        # observed-repair with an unverified mechanism: no model records, not on the registry
        self.att(660, "f_unv", 60.0, source="void f_unv(void){int a=1;}")
        self.att(661, "f_unv", 66.0, source="void f_unv(void){int a=2;}", strategy="mystery-repair")
        self.edge(660, 661)
        # library TU
        self.att(580, "f_lib", 50.0, source="void f_lib(void){int a=1;}")
        self.att(581, "f_lib", 60.0, source="void f_lib(void){int a=2;}")
        self.edge(580, 581)
        # finished function: an exact attempt exists
        self.att(590, "f_finished", 70.0, source="void f_finished(void){int a=1;}")
        self.att(591, "f_finished", 75.0, source="void f_finished(void){int a=2;}")
        self.edge(590, 591)
        # created before its only worse sibling, so it is never a reconstructed pair
        self.att(592, "f_finished", 100.0, exact=1, source="void f_finished(void){}",
                 created=500)
        # sealed members, one per key
        for base, name in ((600, "f_dev"), (610, "f_held"), (620, "f_cluster"), (630, "f_panel")):
            self.att(base, name, 40.0, source=f"void {name}(void){{int a=1;}}")
            self.att(base + 1, name, 55.0, source=f"void {name}(void){{int a=2;}}")
            self.edge(base, base + 1)
        # sealed through a shape the four keys do not reach
        self.att(640, "f_legacy", 40.0, source="void f_legacy(void){int a=1;}")
        self.att(641, "f_legacy", 55.0, source="void f_legacy(void){int a=2;}")
        self.edge(640, 641)
        # duplicate (parent_sha256, child_sha256): identical source text, NULL stored hashes
        self.att(550, "f_dup", 60.0, source="void f_dup(void){int a=1;}", sha=None)
        self.att(551, "f_dup", 70.0, source="void f_dup(void){int a=2;}", sha=None)
        self.edge(550, 551)
        self.att(552, "f_dup", 60.0, source="void f_dup(void){int a=1;}", sha=None)
        self.att(553, "f_dup", 71.0, source="void f_dup(void){int a=2;}", sha=None)
        self.edge(552, 553)
        # empty source code
        self.att(520, "f_empty", 40.0, source="")
        self.att(521, "f_empty", 70.0, source="void f_empty(void){int a=2;}")
        self.edge(520, 521)
        # parent is a different function (the parent attempt lives in f_other, whose
        # single compiled attempt can therefore never be a reconstructed pair)
        self.att(700, "f_other", 50.0, source="void f_other(void){int a=1;}")
        self.att(563, "f_xfunc", 60.0, source="void f_xfunc(void){int a=2;}")
        self.edge(700, 563)
        # child did not compile
        self.att(530, "f_nc", 40.0, source="void f_nc(void){int a=1;}")
        self.att(531, "f_nc", 70.0, compiled=0, source="void f_nc(void){int a=2;}")
        self.edge(530, 531)
        # both compiled but not improving
        self.att(540, "f_ni", 90.0, source="void f_ni(void){int a=1;}")
        self.att(541, "f_ni", 90.0, source="void f_ni(void){int a=2;}")
        self.edge(540, 541)
        # a strict improvement smaller than the old 0.5 margin: one instruction of many
        self.att(670, "f_small", 70.0, source="void f_small(void){int a=1;}")
        self.att(671, "f_small", 70.2, source="void f_small(void){int a=2;}", strategy="repair-d1")
        self.edge(670, 671, relation="deterministic-repair")
        # a reference seed and a repair two steps below it
        self.att(800, "f_hist", 60.0, source="void f_hist(void){int a=1;}",
                 strategy="campaign-intake:explicit-historical-seed")
        self.att(801, "f_hist", 70.0, source="void f_hist(void){int a=2;}", strategy="repair-d1")
        self.edge(800, 801, relation="deterministic-repair")
        self.att(802, "f_hist", 75.0, source="void f_hist(void){int a=3;}", strategy="repair-d2")
        self.edge(801, 802, relation="deterministic-repair")
        # reconstructed: three lineage-free compiled candidates of one function
        self.att(500, "f_recon", 50.0, source="void f_recon(void){int a=1;}", diff="low",
                 created=100, sha=None)
        self.att(501, "f_recon", 80.0, source="void f_recon(void){int a=2;}", diff="mid",
                 created=200, sha=None)
        self.att(502, "f_recon", 85.0, source="void f_recon(void){int a=3;}", diff="high",
                 created=300, sha=None)
        # reconstructed whose only worse predecessor has no stored feedback
        self.att(510, "f_nofb", 40.0, source="void f_nofb(void){int a=1;}", diff="", created=100)
        self.att(511, "f_nofb", 90.0, source="void f_nofb(void){int a=2;}", diff="d", created=200)

    # -- sealed sets, workspace -------------------------------------------
    def _sealed_sets(self) -> None:
        (self.sets / "fixture_v1.json").write_text(json.dumps({
            "kind": "fixture",
            "dev": [{"function": "f_dev"}],
            "heldout": [{"function": "f_held"}],
            "cluster": [{"function": "f_cluster", "tu_id": 5}],
            "panel": [{"function": "f_panel"}],
        }), encoding="utf-8")
        # the shape the four sealed keys do not reach (completion-campaign-dev-seeds-v1.json)
        (self.sets / "campaign_seeds_v1.json").write_text(json.dumps({
            "f_legacy": 640, "schema_version": 1,
        }), encoding="utf-8")

    def _target_asm(self) -> None:
        for name in ("f_ok", "f_det", "f_prop", "f_unv", "f_dup"):
            directory = self.workspace / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "target.s").write_text(
                "/* macro preamble, not part of the body */\n"
                f"glabel {name}\n"
                f"    addiu   sp,sp,-0x10\n    jr      ra\n    nop\n"
                f"endlabel {name}\n"
                "/* trailing material */\n", encoding="utf-8")
            (directory / ".compiler-target.json").write_text(
                json.dumps({"function": name, "target": TU_OK}), encoding="utf-8")
            (directory / ".compiler-abc123.json").write_text(json.dumps({
                "schema_version": 1, "target": TU_OK,
                "settings": {"IDO_CC": "tools/ido-recomp/linux/cc", "CFLAGS": "-c -mips1 -G 0",
                             "C_OPT": "-O2", "C_MIPS": "-mips1"}}), encoding="utf-8")

    # -- helpers -----------------------------------------------------------
    def export(self, **kwargs):
        kwargs.setdefault("sets_dir", self.sets)
        kwargs.setdefault("workspace_root", self.workspace)
        kwargs.setdefault("seed", 20260920)
        kwargs.setdefault("reconstructed_per_function", 0)
        result = rd.export_dataset(self.conn, **kwargs)
        return result

    def records(self, **kwargs) -> list[dict]:
        return self.export(**kwargs)["records"]

    def by_function(self, records, name):
        return [r for r in records if r["function"] == name]

    def filter_counts(self, audit) -> dict:
        return {entry["filter"]: entry for entry in audit["filters"]}


@pytest.fixture()
def fx(tmp_path: Path) -> Fixture:
    fixture = Fixture(tmp_path)
    yield fixture
    fixture.conn.close()


# --- provenance ---------------------------------------------------------------

def test_all_three_provenance_tiers_are_produced(fx: Fixture) -> None:
    records = fx.records()
    tiers = {record["provenance"] for record in records}
    assert tiers == {"observed-repair", "deterministic-repair", "reconstructed-lineage"}


def test_observed_repair_requires_a_real_edge_with_model_records(fx: Fixture) -> None:
    records = fx.by_function(fx.records(), "f_ok")
    record = next(r for r in records if r["meta"]["child_attempt_id"] == 561)
    assert record["provenance"] == "observed-repair"
    assert record["meta"]["lineage"] == "observed"
    assert record["meta"]["relation"] == "model-repair"
    assert record["meta"]["mechanism_confidence"] == "model-records"
    assert "attempts.raw_response" in record["meta"]["generator"]["mechanism_evidence"]
    assert record["meta"]["reconstruction_basis"] is None
    assert "lineage_note" not in record["meta"]
    assert record["meta"]["score_delta"] == pytest.approx(5.0)


def test_model_proposal_row_alone_is_model_evidence(fx: Fixture) -> None:
    record = fx.by_function(fx.records(), "f_prop")[0]
    assert record["provenance"] == "observed-repair"
    assert record["meta"]["mechanism_confidence"] == "model-records"
    assert "model_proposals.child_attempt_id" in record["meta"]["generator"]["mechanism_evidence"]


def test_deterministic_repair_is_labelled_with_the_exact_mechanism(fx: Fixture) -> None:
    record = fx.by_function(fx.records(), "f_det")[0]
    assert record["provenance"] == "deterministic-repair"
    assert record["meta"]["lineage"] == "observed"
    assert record["meta"]["generator"]["strategy"] == "alloc-order"
    assert record["meta"]["generator"]["mechanism_family"] == "alloc-order"
    assert record["meta"]["mechanism_confidence"] == "deterministic-registry"
    evidence = " ".join(record["meta"]["generator"]["mechanism_evidence"])
    assert "eval/allocsearch.py" in evidence


def test_unregistered_mechanism_is_never_called_deterministic(fx: Fixture) -> None:
    record = fx.by_function(fx.records(), "f_unv")[0]
    assert record["provenance"] == "observed-repair"
    assert record["meta"]["mechanism_confidence"] == "unverified"
    assert record["meta"]["generator"]["family"] == "unverified"
    assert record["meta"]["generator"]["mechanism_family"] is None


def test_reconstructed_pair_is_labelled_reconstructed(fx: Fixture) -> None:
    records = fx.by_function(fx.records(), "f_recon")
    assert records, "the fixture's lineage-free pair must be exported"
    for record in records:
        assert record["provenance"] == "reconstructed-lineage"
        assert record["meta"]["lineage"] == "reconstructed"
        assert record["meta"]["reconstruction_basis"] == rd.RECONSTRUCTED_BASIS
        assert record["meta"]["relation"] is None
        assert record["meta"]["action"] is None
        note = record["meta"]["lineage_note"]
        assert "inferred" in note and "NOT the original model interaction" in note
    pairs = {(r["meta"]["parent_attempt_id"], r["meta"]["child_attempt_id"]) for r in records}
    assert pairs == {(500, 501), (501, 502)}, "one parent per child, best strictly worse"
    assert all(r["target"]["score"] > r["input"]["compiler_outcome"]["score"] + 0.5
               for r in records)


def test_reconstructed_output_never_claims_the_original_interaction(fx: Fixture) -> None:
    rebuilt = fx.by_function(fx.records(), "f_recon")
    assert rebuilt
    assert all(r["provenance"] != "observed-repair" for r in rebuilt)
    assert all(r["meta"]["lineage"] != "observed" for r in rebuilt)


# --- exclusions ---------------------------------------------------------------

def test_sealed_dev_and_heldout_members_are_excluded(fx: Fixture) -> None:
    result = fx.export()
    functions = {r["function"] for r in result["records"]}
    assert "f_dev" not in functions and "f_held" not in functions
    counts = fx.filter_counts(result["audit"])
    assert counts["sealed-dev-heldout"]["removed_here"] >= 2
    excluded = result["audit"]["excluded_function_names"]["sealed-dev-heldout"]
    assert {"f_dev", "f_held"} <= set(excluded)


def test_sealed_cluster_and_panel_members_are_excluded(fx: Fixture) -> None:
    """The latent ``sealed_functions`` gap: cluster/panel members must not leak."""
    result = fx.export()
    functions = {r["function"] for r in result["records"]}
    assert "f_cluster" not in functions
    assert "f_panel" not in functions

    counts = fx.filter_counts(result["audit"])
    assert counts["sealed-cluster-panel"]["removed_here"] >= 2

    excluded = result["audit"]["excluded_function_names"]
    assert {"f_cluster", "f_panel"} <= set(excluded["sealed-cluster-panel"])
    # ... and they are excluded BECAUSE of that key, not because some other filter
    # happened to catch them first.
    assert {"f_cluster", "f_panel"}.isdisjoint(excluded.get("sealed-dev-heldout", []))


def test_sealed_scanner_reads_all_four_keys(fx: Fixture) -> None:
    members = rd.sealed_members(fx.sets)["members"]
    assert members["dev"] == {"f_dev"}
    assert members["heldout"] == {"f_held"}
    assert members["cluster"] == {"f_cluster"}
    assert members["panel"] == {"f_panel"}
    sealed = rd.sealed_functions(fx.sets)
    assert {"f_dev", "f_held", "f_cluster", "f_panel"} <= sealed


def test_sealed_scanner_does_not_harvest_nested_string_values(tmp_path: Path) -> None:
    """A row's residual diff text is not a function name.

    Sealing by accident is not conservative here: an over-collected name silently
    removes real records from the dataset, and nothing in the export would say why.
    """
    sets = tmp_path / "sets"
    sets.mkdir()
    (sets / "nested.json").write_text(json.dumps({
        "panel": [{"function": "f_panel", "fresh_residual": {
            "first_difference": ["-lw    v1,0(a0)", "+lw    v1,0(a0)"],
            "compiler_error_signature": "",
            "faults": {"structural": 0, "layout": 3}}}],
    }), encoding="utf-8")
    members = rd.sealed_members(sets)["members"]
    assert members["panel"] == {"f_panel"}
    assert "" not in members["panel"]
    assert "-lw    v1,0(a0)" not in members["panel"]
    assert "structural" not in members["panel"]


def test_unrecognized_eval_set_shape_is_excluded_and_counted(fx: Fixture) -> None:
    result = fx.export()
    assert "f_legacy" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["sealed-unrecognized-shape"]["removed_here"] >= 1
    assert "f_legacy" in result["audit"]["excluded_function_names"]["sealed-unrecognized-shape"]
    assert result["audit"]["sealed"]["unrecognized_shape_files"] == {
        "campaign_seeds_v1.json": ["f_legacy"]}


def test_library_translation_units_are_excluded(fx: Fixture) -> None:
    result = fx.export()
    assert "f_lib" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["library-tu"]["removed_here"] >= 1
    assert result["audit"]["policy"]["library_tu_patterns"] == list(rd.exclude_tu_patterns()[0])


def test_finished_function_is_kept_and_tagged_by_default(fx: Fixture) -> None:
    """A finished function's route to a match is the best-labelled supply there is."""
    result = fx.export()
    records = fx.by_function(result["records"], "f_finished")
    assert [(r["meta"]["parent_attempt_id"], r["meta"]["child_attempt_id"]) for r in records]         == [(590, 591)]
    assert records[0]["meta"]["function_finished"] is True
    assert all(r["meta"]["function_finished"] is False
               for r in result["records"] if r["function"] != "f_finished")
    assert result["audit"]["dataset"]["records_from_finished_functions"] == 1
    assert "function-has-exact-attempt" in result["audit"]["policy"]["disabled_filters"]
    assert fx.filter_counts(result["audit"])["function-has-exact-attempt"]["removed_here"] == 0


def test_exclude_finished_restores_the_filter(fx: Fixture) -> None:
    result = fx.export(enabled_filters={"function-has-exact-attempt"})
    assert "f_finished" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["function-has-exact-attempt"]["removed_here"] >= 1
    assert "f_finished" in result["audit"]["excluded_function_names"]["function-has-exact-attempt"]
    # the exact attempt is not itself a record
    assert all(r["meta"]["parent_attempt_id"] != 592 and r["meta"]["child_attempt_id"] != 592
               for r in result["records"])


def test_edge_with_an_uncompiled_endpoint_is_excluded(fx: Fixture) -> None:
    result = fx.export()
    assert "f_nc" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["parent-or-child-not-compiled"]["removed_here"] >= 1
    assert result["audit"]["kb_edges"]["edges_either_not_compiled"] >= 1


def test_non_improving_edge_is_excluded(fx: Fixture) -> None:
    result = fx.export()
    assert "f_ni" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["not-improving-edge"]["removed_here"] >= 1
    assert counts["not-improving-edge"]["improving_removed_here"] == 0


def test_strict_improvement_below_the_old_margin_is_kept(fx: Fixture) -> None:
    """70.0 -> 70.2 is a verified improvement: scores come from one scorer in one session."""
    record = fx.by_function(fx.records(), "f_small")[0]
    assert record["meta"]["score_delta"] == pytest.approx(0.2)
    assert record["provenance"] == "deterministic-repair"
    assert "f_small" not in {r["function"] for r in fx.records(edge_epsilon=0.5)}


def test_unknown_filter_name_is_refused(fx: Fixture) -> None:
    with pytest.raises(ValueError):
        fx.export(enabled_filters={"function-has-exact-attempts"})
    with pytest.raises(ValueError):
        fx.export(disabled_filters={"no-such-filter"})


def test_cross_function_parent_is_excluded(fx: Fixture) -> None:
    result = fx.export()
    assert "f_xfunc" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["parent-child-different-function"]["removed_here"] >= 1


def test_empty_source_code_is_excluded(fx: Fixture) -> None:
    result = fx.export()
    assert "f_empty" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["empty-source-code"]["removed_here"] >= 1


def test_reconstructed_pair_without_feedback_is_excluded(fx: Fixture) -> None:
    result = fx.export()
    assert "f_nofb" not in {r["function"] for r in result["records"]}
    counts = fx.filter_counts(result["audit"])
    assert counts["missing-parent-feedback"]["removed_here"] == 1
    # the pair WAS formed (so the filter, not the pairing rule, is what removed it)
    assert result["audit"]["reconstructed"]["pairs"] == 3
    assert result["audit"]["reconstructed"]["children_without_parent"] >= 1
    assert "f_nofb" in result["audit"]["excluded_function_names"]["missing-parent-feedback"]


def test_duplicate_parent_child_sha_pairs_collapse(fx: Fixture) -> None:
    result = fx.export()
    duplicates = fx.by_function(result["records"], "f_dup")
    assert len(duplicates) == 1
    keys = {(r["meta"]["parent_sha256"], r["meta"]["child_sha256"]) for r in duplicates}
    assert len(keys) == 1
    counts = fx.filter_counts(result["audit"])
    assert counts["duplicate-pair"]["removed_here"] == 1
    assert duplicates[0]["meta"]["parent_sha256"] == _sha("void f_dup(void){int a=1;}")
    assert duplicates[0]["meta"]["parent_sha256_source"] == "computed-from-source_code"


# --- assembly and schema ------------------------------------------------------

def test_records_without_target_assembly_are_marked(fx: Fixture) -> None:
    result = fx.export()
    missing = [r for r in result["records"] if not r["input"]["target_asm_available"]]
    assert missing, "f_recon and friends have no target.s in the fixture workspace"
    for record in missing:
        assert record["input"]["target_asm"] is None
        assert record["input"]["target_asm_note"]
        assert record["input"]["target_asm_source"].endswith("target.s")
    audit = result["audit"]["dataset"]
    assert audit["target_asm_missing"] == len(missing)
    assert audit["target_asm_available"] == len(result["records"]) - len(missing)
    assert set(audit["target_asm_missing_functions"]) == {r["function"] for r in missing}


def test_available_target_assembly_is_the_body_only(fx: Fixture) -> None:
    record = next(r for r in fx.by_function(fx.records(), "f_ok")
                  if r["input"]["target_asm_available"])
    asm = record["input"]["target_asm"]
    assert asm.startswith("glabel f_ok")
    # like solver.workspace.target_asm, the body stops AT endlabel and excludes the
    # label itself, the macro preamble, and anything after the function
    assert "endlabel" not in asm
    assert "macro preamble" not in asm and "trailing material" not in asm
    assert record["input"]["recipe"]["c_opt"] == "-O2"
    assert record["input"]["flags"] == "-c -mips1 -G 0 -O2 -mips1"


def test_target_body_extraction_matches_solver_workspace(fx: Fixture) -> None:
    """The reimplementation must agree with ``solver.workspace.target_asm``."""
    from solver.workspace import target_asm as solver_target_asm
    body = rd.target_body((fx.workspace / "f_ok" / "target.s").read_text(encoding="utf-8"),
                          "f_ok")
    assert body == solver_target_asm(fx.workspace / "f_ok", "f_ok")
    with pytest.raises(RuntimeError):
        rd.target_body("no labels here", "f_ok")


def test_declarations_are_null_with_a_stated_reason(fx: Fixture) -> None:
    for record in fx.records():
        assert record["input"]["declarations"] is None
        assert "no declaration context" in record["input"]["declarations_note"]


def test_record_carries_the_required_schema(fx: Fixture) -> None:
    records = fx.records()
    record = fx.by_function(records, "f_ok")[0]
    assert record["game"] == "sbk1"
    assert record["id"] == "sbk1:f_ok:560->561"
    assert record["func_addr"] == fx.addr["f_ok"]
    assert record["tu"] == TU_OK
    assert record["input"]["compiler"] == "ido-5.3"
    assert record["input"]["candidate_c"].startswith("void f_ok")
    outcome = record["input"]["compiler_outcome"]
    assert outcome["compiled"] is True and outcome["score"] == 90.0
    assert outcome["diff"] == "from ok parent"
    assert record["target"]["score"] == 95.0 and record["target"]["exact"] is False
    assert record["meta"]["parent_sha256"] and record["meta"]["child_sha256"]
    # every documented schema addition must appear on at least one record
    paths: set[str] = set()
    for item in records:
        for key in ("input", "meta"):
            for name in item[key]:
                paths.add(f"{key}.{name}")
                if isinstance(item[key][name], dict):
                    for nested in item[key][name]:
                        paths.add(f"{key}.{name}.{nested}")
    assert set(rd.SCHEMA_ADDITIONS) <= paths


# --- filtered supply accounting ----------------------------------------------

def test_filter_funnel_is_additive_and_reports_improving_edges(fx: Fixture) -> None:
    result = fx.export()
    audit = result["audit"]
    total_removed = sum(entry["removed_here"] for entry in audit["filters"])
    assert audit["candidates_before_filters"] - total_removed == \
        audit["candidates"]["after_filters_and_dedup"]
    assert audit["kb_edges"]["improving_edges"] == 18
    assert audit["reconstructed"]["pairs"] == 3
    series = audit["improving_edges_remaining_after_filter"]
    values = [series[name] for name in rd.FILTER_ORDER]
    assert values == sorted(values, reverse=True), "the improving-edge funnel must not grow"
    assert values[0] == 21, "18 improving edges + 3 reconstructed pairs before any filter"
    exported_observed = sum(1 for r in result["records"]
                            if r["provenance"] != "reconstructed-lineage")
    # f_ok f_det f_prop f_unv f_dup f_finished f_small and f_hist's two edges
    assert exported_observed == 9
    assert series["<exported>"] == len(result["records"]) == 11
    # the requirement, restated as an equality on the reported funnel: every
    # improving candidate that no filter removed is in the dataset
    assert series["<exported>"] <= audit["kb_edges"]["improving_edges"] + \
        audit["reconstructed"]["pairs"]


def test_every_filter_is_load_bearing(fx: Fixture) -> None:
    """Each filter must FIRE on its motivating case and change the exported set."""
    baseline = fx.export(enabled_filters=rd.DEFAULT_OFF_FILTERS)
    baseline_ids = {r["id"] for r in baseline["records"]}
    counts = fx.filter_counts(baseline["audit"])
    for name in rd.FILTER_ORDER:
        assert counts[name]["removed_here"] >= 1, f"{name} never fired on the fixture"
        mutated = fx.export(enabled_filters=rd.DEFAULT_OFF_FILTERS, disabled_filters={name})
        mutated_ids = {r["id"] for r in mutated["records"]}
        assert mutated_ids > baseline_ids, (
            f"disabling {name} changed nothing: the filter is not load-bearing on the "
            f"case it exists for")


def test_reconstructed_budget_is_reported_not_hidden(fx: Fixture) -> None:
    limited = fx.export(reconstructed_per_function=1)
    assert len(fx.by_function(limited["records"], "f_recon")) == 1
    assert limited["audit"]["budgets"]["reconstructed_dropped_by_budget_total"] == 1
    unlimited = fx.export(reconstructed_per_function=0)
    assert len(fx.by_function(unlimited["records"], "f_recon")) == 2


def test_limit_is_deterministic(fx: Fixture) -> None:
    first = [r["id"] for r in fx.export(limit=3)["records"]]
    second = [r["id"] for r in fx.export(limit=3)["records"]]
    assert first == second and len(first) == 3


# --- splits -------------------------------------------------------------------

def test_splits_are_function_disjoint_and_tu_coherent(fx: Fixture) -> None:
    result = fx.export()
    per_split: dict[str, set[str]] = {}
    for record in result["records"]:
        per_split.setdefault(record["split"], set()).add(record["function"])
        assert record["split"] in rd.SPLIT_NAMES
    names = list(per_split)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            assert per_split[left].isdisjoint(per_split[right]), \
                f"{left} and {right} share a function"

    tu_of = {row["name"]: row["tu"] for row in fx_export_rows(fx)}
    for split, functions in per_split.items():
        for name in functions:
            record_tu = next(r["tu"] for r in result["records"] if r["function"] == name)
            assert tu_of[name] == record_tu, "function TU changed between records"
            for other in result["records"]:
                if other["tu"] == record_tu:
                    assert other["split"] == split, "one TU leaked across splits"

    manifest = result["splits"]
    listed = set().union(*(set(manifest["splits"][s]["functions"]) for s in rd.SPLIT_NAMES))
    assert listed == {r["function"] for r in result["records"]}
    assert manifest["split_digest"] and manifest["unit"] == "translation-unit"
    again = fx.export()["splits"]["split_digest"]
    assert again == manifest["split_digest"]
    other = fx.export(seed=7)["splits"]["split_digest"]
    assert other != manifest["split_digest"]


def fx_export_rows(fx: Fixture) -> list[dict]:
    return [dict(row) for row in fx.conn.execute(
        "select f.name, f.addr, t.name as tu from functions f join tus t on t.id = f.tu_id")]


def test_split_for_is_a_pure_function_of_tu_and_seed() -> None:
    assert rd.split_for("build/src/a.o", "f", 1) == rd.split_for("build/src/a.o", "g", 1)
    assert {rd.split_for(f"build/src/tu{i}.o", f"f{i}", 20260920) for i in range(400)} == \
        set(rd.SPLIT_NAMES)
    assert rd.split_for(None, "f_no_tu", 5) == rd.split_for(None, "f_no_tu", 5)


# --- manifest -----------------------------------------------------------------

def test_manifest_carries_the_split_digest_and_full_audit(fx: Fixture, tmp_path: Path) -> None:
    result = fx.export()
    audit_path = tmp_path / "audit.json"
    manifest = rd.write_dataset(result, tmp_path / "dataset", kb=fx.db, seed=20260920,
                                audit_path=audit_path)
    on_disk = json.loads((tmp_path / "dataset" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["manifest_digest"] == manifest["manifest_digest"]
    assert on_disk["splits"]["split_digest"] == result["splits"]["split_digest"]
    assert on_disk["audit"]["filters"] == result["audit"]["filters"]
    assert on_disk["audit"]["dataset"]["records"] == len(result["records"])
    lines = (tmp_path / "dataset" / "repair_dataset.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == len(result["records"])
    assert on_disk["files"]["jsonl_sha256"] == hashlib.sha256(
        (tmp_path / "dataset" / "repair_dataset.jsonl").read_bytes()).hexdigest()
    assert json.loads(audit_path.read_text(encoding="utf-8"))["dataset"]["records"] == \
        len(result["records"])


def test_drop_reason_reports_the_first_matching_filter(fx: Fixture) -> None:
    """The public filter API: first match wins, and the reason is the filter's own."""
    ctx = rd.build_context(fx.conn, sets_dir=fx.sets, workspace_root=fx.workspace,
                           seed=20260920)
    row = {"parent_id": 530, "child_id": 531, "parent_addr": fx.addr["f_nc"],
           "child_addr": fx.addr["f_nc"], "parent_compiled": 1, "child_compiled": 0,
           "is_improving": False, "has_edge": True, "parent_source_len": 5,
           "child_source_len": 5, "parent_diff_len": 1, "parent_sha": None, "child_sha": None,
           "parent_score": 40.0, "child_score": 70.0}
    reason, text = rd.drop_reason(ctx, row, "f_nc", "build/src/game/nc.o")
    assert reason == "parent-or-child-not-compiled"
    assert text == rd.FILTER_REASONS[reason]
    row["child_compiled"] = 1
    reason, _ = rd.drop_reason(ctx, row, "f_nc", "build/src/game/nc.o")
    assert reason == "not-improving-edge", "the next owning filter takes over"
    assert rd.drop_reason(ctx, row, "f_nc", "build/src/game/nc.o",
                          disabled={"not-improving-edge"})[0] is None


def test_manifest_digest_is_stable_across_runs(fx: Fixture, tmp_path: Path) -> None:
    """The digest is content identity: two identical exports must agree.

    It must not hash its own wall-clock stamp, which would make it change every run
    and therefore verify nothing.
    """
    first = rd.write_dataset(fx.export(), tmp_path / "one", kb=fx.db, seed=20260920)
    second = rd.write_dataset(fx.export(), tmp_path / "two", kb=fx.db, seed=20260920)
    assert first["manifest_digest"] == second["manifest_digest"]
    assert first["files"]["jsonl_sha256"] == second["files"]["jsonl_sha256"]
    other_seed = rd.write_dataset(fx.export(seed=99), tmp_path / "three", kb=fx.db, seed=99)
    assert other_seed["manifest_digest"] != first["manifest_digest"]
    on_disk = json.loads((tmp_path / "one" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["manifest_digest"] == rd._json_digest(on_disk)
    assert on_disk["split_digest"] == on_disk["splits"]["split_digest"]


def test_audit_only_mode_agrees_with_the_export(fx: Fixture) -> None:
    audit = rd.audit_filters(fx.conn, sets_dir=fx.sets, workspace_root=fx.workspace,
                             seed=20260920, reconstructed_per_function=0)
    result = fx.export()
    assert audit["dataset"]["records"] == result["audit"]["dataset"]["records"]
    assert audit["filters"] == result["audit"]["filters"]


# --- pure helpers -------------------------------------------------------------

def test_like_in_matches_clean_set_pattern_semantics() -> None:
    assert rd.like_in("%libmus%", "build/src/libmus/player.o")
    assert not rd.like_in("%libmus%", "build/src/race/player/race_player_update.o")
    assert rd.like_in("%ultra%", "build/src/ULTRA/audio/synsetfxmix.o")


def test_mechanism_registry_is_exact_not_fuzzy() -> None:
    assert rd.mechanism_for("alloc-order").family == "alloc-order"
    assert rd.mechanism_for("do-restore:restore do-while at 2435").family == "do-restore"
    assert rd.mechanism_for("repair-d2").family == "rewrite-beam"
    assert rd.mechanism_for("repair-d7") is None, "solver/repair.py stops at max_depth=6"
    assert rd.mechanism_for("repair-d") is None
    assert rd.mechanism_for("modelrepair-d1") is None
    assert rd.mechanism_for("modelrepair-normalize:c89").family == "compiler-normalization"
    assert rd.mechanism_for("agentrepair-compile-recovery:assembly-byteview-redraft").family         == "compile-recovery"
    assert rd.mechanism_for("agentrepair-stack-layout").family == "stack-layout"
    assert rd.mechanism_for("agentrepair-context-projection") is None
    assert rd.mechanism_for("typed-semantic-gradient-beam").family == \
        "typed-semantic-gradient-beam"


# --- assistance tags ----------------------------------------------------------

def _build_tree(root: Path) -> Path:
    tree = root / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "include" / "game").mkdir(parents=True)
    (tree / "src" / "a.c").write_text(
        "typedef struct { int x; } RefOnly;\ntypedef struct { int y; } Shared;\n",
        encoding="utf-8")
    (tree / "include" / "game" / "shared.h").write_text(
        "typedef struct { int y; } Shared;\ntypedef struct { int z; } GameOnly;\n"
        "void gameCall(GameOnly *g);\n", encoding="utf-8")
    (tree / "include" / "common.h").write_text(
        "typedef struct { int y; } Shared;\nvoid sdkCall(void);\n", encoding="utf-8")
    return tree


def test_reference_only_types_are_tagged_per_endpoint(fx: Fixture, tmp_path: Path) -> None:
    fx.conn.execute("update attempts set source_code=? where id=561",
                    ("/* RefOnly in a comment is not use */\n#include \"game/shared.h\"\n"
                     "void f_ok(void){ RefOnly r; Shared s; }",))
    fx.conn.commit()
    records = fx.records(build_tree=_build_tree(tmp_path))
    ok = next(r for r in records if r["meta"]["child_attempt_id"] == 561)
    tags = ok["meta"]["assistance"]
    assert tags["reference_types_checked"] is True
    assert tags["child_reference_types"] == ["RefOnly"], "Shared has a header, so it is not one"
    assert tags["parent_reference_types"] == []
    assert tags["child_game_header_types"] == [], "Shared is also declared outside game/"
    assert ok["meta"]["clean"] is False
    det = fx.by_function(records, "f_det")[0]
    assert det["meta"]["clean"] is True


def test_reference_seed_lineage_reaches_descendants(fx: Fixture, tmp_path: Path) -> None:
    records = fx.by_function(fx.records(build_tree=_build_tree(tmp_path)), "f_hist")
    by_child = {r["meta"]["child_attempt_id"]: r for r in records}
    assert set(by_child) == {801, 802}
    assert by_child[801]["meta"]["assistance"]["parent_reference_seed_lineage"] is True
    assert by_child[802]["meta"]["assistance"]["parent_reference_seed_lineage"] is True,         "the seed is a grandparent: the tag must follow lineage, not the parent's own strategy"
    assert by_child[802]["meta"]["clean"] is False


def test_unchecked_reference_types_are_never_reported_clean(fx: Fixture) -> None:
    result = fx.export()
    assert all(r["meta"]["clean"] is None for r in result["records"])
    assert all(r["meta"]["assistance"]["child_reference_types"] is None
               for r in result["records"])
    assert set().union(*(counts for counts in result["audit"]["dataset"]["clean_by_split"]
                         .values())) == {"unchecked"}


def test_a_build_tree_with_no_reference_types_is_refused(fx: Fixture, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    (empty / "src").mkdir(parents=True)
    (empty / "include").mkdir()
    with pytest.raises(ValueError):
        fx.export(build_tree=empty)


def test_game_header_use_is_measured_by_use_not_by_include(fx: Fixture, tmp_path: Path) -> None:
    """common.h reaches game/ headers transitively, so an absent #include proves nothing."""
    fx.conn.execute("update attempts set source_code=? where id=571",
                    ('#include "common.h"\n/* GameOnly in a comment is not use */\n'
                     "void f_det(void){ GameOnly g; gameCall(&g); sdkCall(); }",))
    fx.conn.execute("update attempts set source_code=? where id=651",
                    ('#include "game/shared.h"\nvoid f_prop(void){ int a=2; }',))
    fx.conn.commit()
    records = fx.records(build_tree=_build_tree(tmp_path))
    det = fx.by_function(records, "f_det")[0]["meta"]
    assert det["assistance"]["child_game_header_types"] == ["GameOnly"]
    assert det["assistance"]["child_game_header_prototypes"] == ["gameCall"]
    assert det["clean"] is False
    prop = fx.by_function(records, "f_prop")[0]["meta"]
    assert prop["assistance"]["child_game_header_types"] == []
    assert prop["clean"] is True, "an include whose declarations go unused is not assistance"


def test_a_function_defining_itself_is_not_header_assistance(fx: Fixture, tmp_path: Path) -> None:
    tree = _build_tree(tmp_path)
    (tree / "include" / "game" / "own.h").write_text("void f_det(void);\n", encoding="utf-8")
    det = fx.by_function(fx.records(build_tree=tree), "f_det")[0]["meta"]
    assert det["assistance"]["child_game_header_prototypes"] == []
    assert det["clean"] is True


def test_game_header_identifiers_exclude_names_declared_elsewhere(tmp_path: Path) -> None:
    names = rd.game_header_identifiers(_build_tree(tmp_path))
    assert names["types"] == {"GameOnly"}
    assert names["prototypes"] == {"gameCall"}


def test_parent_only_header_use_is_not_clean(fx: Fixture, tmp_path: Path) -> None:
    """Removing an assisted call cannot erase assistance in the training input."""
    fx.conn.execute("update attempts set source_code=? where id=570",
                    ('#include "common.h"\nvoid f_det(void){ gameCall(0); }',))
    fx.conn.commit()
    result = fx.export(build_tree=_build_tree(tmp_path))
    det = fx.by_function(result["records"], "f_det")[0]["meta"]
    assert det["clean"] is False
    assert det["assistance"]["parent_game_header_prototypes"] == ["gameCall"]
    assert det["assistance"]["child_game_header_prototypes"] == []
    assert result["audit"]["dataset"]["assistance"]["parent_game_header_prototypes"] == 1


def test_reference_recovered_child_is_not_clean(fx: Fixture, tmp_path: Path) -> None:
    """A recovered answer is assisted even when its recorded parent is independent."""
    fx.conn.execute("update attempts set strategy=? where id=571", ("history-recovery",))
    fx.conn.commit()
    result = fx.export(build_tree=_build_tree(tmp_path))
    det = fx.by_function(result["records"], "f_det")[0]["meta"]
    assert det["clean"] is False
    assert det["assistance"]["child_reference_seed_lineage"] is True
    assert det["assistance"]["parent_reference_seed_lineage"] is False
    assert result["audit"]["dataset"]["assistance"]["child_reference_seed_lineage"] >= 1
