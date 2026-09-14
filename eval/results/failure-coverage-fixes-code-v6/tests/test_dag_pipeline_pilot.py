from pathlib import Path
from types import SimpleNamespace

from eval import dag_pipeline_pilot as pilot
from solver import mips_differential as differential, workspace


def test_fresh_compile_failure_does_not_require_assembly_dumps(tmp_path, monkeypatch):
    import hashlib
    import json
    import sqlite3
    db = tmp_path / "kb.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("create table functions(name text); insert into functions values('fresh');")
    conn.close()
    source = "invalid C"
    manifest = {"cluster": [{"function": "fresh", "attempt_id": 1,
                             "source_sha256": hashlib.sha256(source.encode()).hexdigest()}]}
    manifest["manifest_digest"] = pilot.logic_first._digest(manifest)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(pilot.agentrepair, "_refuse_frozen_heldout", lambda *_: None)
    monkeypatch.setattr(pilot.agentrepair, "_source_for_attempt", lambda *_: source)
    monkeypatch.setattr(pilot.refine, "ensure_schema", lambda *_: None)
    monkeypatch.setattr(pilot.callgraph, "edges", lambda *_: ({}, {}))
    monkeypatch.setattr(pilot.matched, "already_matched", lambda *_: set())
    monkeypatch.setattr(workspace, "bootstrap", lambda *_: tmp_path)
    monkeypatch.setattr(workspace, "score", lambda *a, **k: workspace.Attempt(
        compiled=False, score=0, exact=False, diff="", compiler_stderr="bad declaration",
        raw_output="", receipt_id=2))
    result = pilot.run(repo=tmp_path, db=db, sets=tmp_path, manifest_path=path,
                       output=tmp_path / "out.json", max_cases=10)
    assert result["aggregate"]["stop_stages"] == {"compilation": 1}
    assert not result["dag"]["nodes"][0]["semantic_authoritative"]


def _attempt(*, exact=False):
    return workspace.Attempt(
        compiled=True, score=90.0, exact=exact, diff="diff",
        compiler_stderr="", raw_output="")


def _report(*, complete=True):
    return SimpleNamespace(
        complete=complete,
        missing_instructions=() if complete else (3,),
        unresolved_branch_edges=() if complete else ((2, True),))


def _stage(*, calls=(), row_statuses=("passed",)):
    returned = [SimpleNamespace(status="returned", error="")]
    rows = [SimpleNamespace(status=status) for status in row_statuses]
    return pilot._stop_stage(
        attempt=_attempt(), target_report=_report(),
        candidate_report=_report(), target_runs=tuple(returned),
        candidate_runs=returned, rows=rows, calls=list(calls),
        unresolved_contracts=[], input_issues=[], unsettled_callees=[])


def test_leaf_semantic_pass_routes_to_byte_exactness():
    assert _stage()[0] == "byte_exactness"


def test_nonleaf_concrete_failure_routes_to_provisional_semantic_repair():
    stage, reason = _stage(calls=("callee",), row_statuses=("failed",))

    assert stage == "semantic_repair"
    assert "provisional non-leaf" in reason


def test_nonleaf_hooked_pass_waits_for_real_callee_side_effects():
    assert _stage(calls=("callee",))[0] == "callee_side_effect_models"


def test_concrete_failure_routes_to_repair_before_coverage_is_complete():
    returned = [SimpleNamespace(status="returned", error="")]
    stage, reason = pilot._stop_stage(
        attempt=_attempt(), target_report=_report(complete=False),
        candidate_report=_report(), target_runs=tuple(returned),
        candidate_runs=returned, rows=[SimpleNamespace(status="failed")],
        calls=[], unresolved_contracts=[], input_issues=[],
        unsettled_callees=[])

    assert stage == "semantic_repair"
    assert "coverage remains partial" in reason


def test_passing_partial_panel_still_routes_to_target_coverage():
    returned = [SimpleNamespace(status="returned", error="")]
    stage, _reason = pilot._stop_stage(
        attempt=_attempt(), target_report=_report(complete=False),
        candidate_report=_report(), target_runs=tuple(returned),
        candidate_runs=returned, rows=[SimpleNamespace(status="passed")],
        calls=[], unresolved_contracts=[], input_issues=[],
        unsettled_callees=[])

    assert stage == "target_coverage"


def test_nonreturn_terminal_counts_as_executable():
    nonreturn = [SimpleNamespace(status="nonreturn", error="")]
    stage, _reason = pilot._stop_stage(
        attempt=_attempt(), target_report=_report(),
        candidate_report=_report(), target_runs=tuple(nonreturn),
        candidate_runs=nonreturn, rows=[SimpleNamespace(status="passed")],
        calls=[], unresolved_contracts=[], input_issues=[],
        unsettled_callees=[])

    assert stage == "byte_exactness"


def test_pointer_parameters_after_a0_receive_distinct_mapped_regions(tmp_path):
    include = tmp_path / "include"
    include.mkdir()
    (include / "audio.h").write_text(
        "s32 Fwobble(void *state, u8 *bytes);\n", encoding="utf-8")

    abi = pilot.prototype_info(Path(tmp_path), "Fwobble")
    cases = pilot._seed_cases("Fwobble", abi)

    assert abi["issues"] == []
    assert all(dict(case.entry_registers)["a1"] == 0x11000000
               for case in cases)


def test_integer_word_pairs_are_not_counted_as_one_register(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/pairs.h').write_text('u64 product(u64 left, s64 right);\ns32 root(u64 value);\ns32 padded(s32 first, u64 second);\n')
    abi = pilot.prototype_info(tmp_path,'product')
    assert abi['arity'] == 2 and abi['argument_words'] == 4
    assert abi['return_registers'] == ['v0','v1']
    assert abi['mutable_scalar_registers'] == ['a0','a1','a2','a3']
    arities, contracts = pilot._call_contracts(tmp_path,['product','root','padded'])
    assert arities['product'] == 4 and arities['root'] == 2
    assert contracts['root']['arity_known']
    assert not contracts['padded']['arity_known']
    assert contracts['padded']['issues']


def test_headerless_static_function_gets_non_authoritative_binary_abi(tmp_path):
    assembly = """
        lui v0,%hi(gState)
        sw zero,%lo(gState)(v0)
        jr ra
        nop
    """

    abi = pilot.prototype_info(Path(tmp_path), "staticInit", assembly)

    assert not abi["known"]
    assert abi["source"] == "binary-inferred"
    assert abi["issues"] == []
    assert abi["mutable_scalar_registers"] == []
    assert abi["return_registers"] == []


def test_binary_abi_infers_double_input_scalar_hole_and_double_return(tmp_path):
    assembly = """
        beqz a2,done
        li t0,1
        sllv t0,t0,a2
        mtc1 t0,f4
        cvt.d.w f6,f4
        mul.d f12,f12,f6
    done:
        jr ra
        mov.d f0,f12
    """

    abi = pilot.prototype_info(Path(tmp_path), "_privateLdexp", assembly)
    cases = pilot._seed_cases("_privateLdexp", abi)

    assert not abi["known"]
    assert abi["issues"] == []
    assert abi["mutable_scalar_registers"] == ["a2"]
    assert abi["floating_entry_pairs"] == ["f12"]
    assert abi["return_registers"] == ["f0", "f1"]
    assert dict(cases[3].entry_registers)["a2"] == 0x80000000
    high, low = differential._float64_words(1.5)
    assert dict(cases[3].entry_registers)["f12"] == low
    assert dict(cases[3].entry_registers)["f13"] == high


def test_candidate_compile_source_mirrors_project_c_defines(tmp_path):
    (tmp_path / "Makefile").write_text(
        "C_DEFINES = -DLANGUAGE_C -DCOMPILING_LIBULTRA \\\n"
        "            -DBUILD_VERSION=VERSION_I -DF3DEX_GBI\n",
        encoding="utf-8")
    source = '#include "common.h"\nvoid F(void) {}\n'

    compiled = workspace._candidate_compile_source(tmp_path, source)

    assert "#define COMPILING_LIBULTRA 1" in compiled
    assert "#define BUILD_VERSION VERSION_I" in compiled
    assert "#define F3DEX_GBI 1" in compiled
    assert '#line 1 "candidate.c"' in compiled
    assert compiled.endswith(source)
