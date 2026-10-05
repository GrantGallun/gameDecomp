from pathlib import Path

import pytest

from solver.theory_repairs import inspect
from eval.search_replay import digest
from test_void_field_repair import SOURCE, ASM, diagnostic


def target(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    data = bytearray(52)
    data[:6] = b'\x7fELF\x01\x02'
    data[18:20] = (8).to_bytes(2,"big")
    data[36:40] = (0x1000).to_bytes(4,"big")
    (ws / "target.o").write_bytes(data)
    (ws / "target.s").write_text(ASM)
    return ws


def failed(source,diagnostics):
    return {"compiled":False,"exact":False,"score":0,"receipt_id":1,
            "frontend":{"passed":False,"status":"rejected","source_sha256":digest(source),"diagnostics":diagnostics}}


def test_real_void_member_generator_emits_a_guarded_candidate(tmp_path):
    rows = inspect(SOURCE,"f",failed(SOURCE,diagnostic()),repo=tmp_path,workspace=target(tmp_path),redraft=False)
    row = next(r for r in rows if r["id"] == "void_members")
    assert row["status"] == "ready"
    assert '(*(s32 *)((unsigned char *)a + 0xBC))' in row["candidates"][0]["source"]
    assert row["addresses"] == ["members"]


def test_missing_target_evidence_leaves_theory_blocked(tmp_path):
    ws = target(tmp_path)
    (ws / "target.o").unlink()
    rows = inspect(SOURCE,"f",failed(SOURCE,diagnostic()),repo=tmp_path,workspace=ws,redraft=False)
    assert all(r["status"] == "blocked" and not r["candidates"] for r in rows)
    assert any("target" in r["reason"] for r in rows)


def test_stale_source_bound_diagnostics_are_rejected(tmp_path):
    v = failed(SOURCE,diagnostic())
    with pytest.raises(ValueError,match="binding"):
        inspect(SOURCE+"\n","f",v,repo=tmp_path,workspace=target(tmp_path),redraft=False)


def test_header_signature_alternative_is_real_and_preserves_body_view(tmp_path):
    from test_header_signature_view import SOURCE as source
    (tmp_path / "include").mkdir()
    (tmp_path / "include/api.h").write_text("s32 f(Actual *state, u32 value);\n")
    diagnostics = "candidate.c:2:5: error: conflicting types for 'f'\n 2 | " + source.splitlines()[1] + "\n"
    rows = inspect(source,"f",failed(source,diagnostics),repo=tmp_path,workspace=target(tmp_path),redraft=False)
    row = next(r for r in rows if r["id"] == "header_signature")
    assert row["status"] == "ready" and "Actual * gd_abi_arg0" in row["candidates"][0]["source"]
