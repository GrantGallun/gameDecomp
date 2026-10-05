"""Thin, noncompiling adapters to existing guarded intake repair mechanisms."""
from pathlib import Path
import hashlib
import json

from eval.repair_graph import _bindings
from eval.search_replay import digest
from solver import (compile_recovery, frontend_repair, global_field_view,
                    header_signature_view, scalar_member_index, void_field_repair)
from solver.repair_theory import observation


CATALOG = {
    "header_signature":("solver.header_signature_view",["signature"],["included public declaration","same supported o32 widths"]),
    "void_members":("solver.void_field_repair",["members"],["diagnosed void member","unambiguous target offset/width"]),
    "scalar_members":("solver.scalar_member_index",["members"],["diagnosed scalar member","known divisible element width"]),
    "global_fields":("solver.global_field_view",["members"],["included global declaration","target symbol/offset/width"]),
    "frontend_abi":("solver.frontend_repair",["calls","other"],["diagnosed interface mismatch","matching header and target evidence"]),
    "byteview_redraft":("solver.compile_recovery.byteview_redrafts",["signature","members","calls"],["locked public ABI","assembly/header-only reconstruction"]),
}


def inspect(source,function,verdict,*,repo,workspace,redraft=True):
    """Every returned ready candidate was emitted by its actual guarded owner.

    Reuse the source-bound diagnostics already paid for by workspace.score.
    The only optional subprocess work is deterministic m2c draft generation;
    it is retained in the owner report, never called a compiler observation.
    """
    repo,workspace = Path(repo),Path(workspace)
    target,assembly_path = workspace / "target.o",workspace / "target.s"
    target_hash = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    _bindings({"source":source,"source_sha256":digest(source),"verdict":verdict},{"target_sha256":target_hash})
    assembly = assembly_path.read_text() if assembly_path.is_file() else ""
    diagnostic = (verdict.get("frontend") or {}).get("diagnostics") or ""
    present = observation(verdict)["blockers"]
    ready_context = bool(assembly and frontend_repair.big_endian_o32(target))
    rows = []
    for id,(owner,addresses,requires) in CATALOG.items():
        row = {"id":id,"owner":owner,"addresses":addresses,"requires":["source-bound diagnostics","big-endian o32 target",*requires],
            "status":"blocked","reason":"no matching observed blocker","candidates":[],
            "evidence":{"source_sha256":digest(source),"diagnostics_sha256":digest(diagnostic),
                        "target_sha256":target_hash,"assembly_sha256":digest(assembly)}}
        rows.append(row)
        if not ready_context:
            row["reason"] = "missing assembly or big-endian o32 target object"
            continue
        if not set(addresses)&set(present) or verdict["exact"]:
            continue
        if id == "byteview_redraft" and not redraft:
            row["reason"] = "assembly redrafting disabled for this caller"
            continue
        try:
            if id == "header_signature":
                report = header_signature_view.propose(repo,source,function,diagnostic,big_endian_o32=True)
            elif id == "void_members":
                report = void_field_repair.propose(source,function,assembly,diagnostic)
            elif id == "scalar_members":
                child,changes = scalar_member_index.rewrite(source,diagnostic)
                report = {"source":child,"changes":changes}
            elif id == "global_fields":
                report = global_field_view.propose(repo,source,function,diagnostic,assembly)
            elif id == "frontend_abi":
                report = frontend_repair.propose(repo,source,function,diagnostic,big_endian_o32=True,target_assembly=assembly)
            else:
                reports = []
                # Preserve an emitted first alternative if a later one declines.
                for label,child,report in compile_recovery.byteview_redrafts(repo,workspace,function,source):
                    reports.append(report)
                    if child != source:
                        row["candidates"].append({"label":label,"family":id,"source":child,"source_sha256":digest(child)})
                row["report"] = reports
                report = None
            if report is not None:
                row["report"] = report
                child = report.get("source",source)
                if child != source:
                    row["candidates"].append({"label":id,"family":id,"source":child,"source_sha256":digest(child)})
            row["reason"] = "; ".join(str(d) for d in (report or {}).get("declines",[])) or "owner guards did not emit a changed candidate"
        except ValueError as exc:
            row["reason"] = str(exc)
        if row["candidates"]:
            row.update(status="ready",reason="existing guarded owner emitted candidate; compiler test required")
    return json.loads(json.dumps(rows,allow_nan=False))
