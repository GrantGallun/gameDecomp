"""Fixed-code, four-stratum intake probe on functions with no logged attempts.

This probes fresh-function intake, not end-to-end autonomous completion. No
finished function C is fed to m2c; compilation/ABI checks still use project
headers. Formal held-out sets are excluded, not silently consumed as DEV.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from eval import callgraph, dag_pipeline_pilot, frozen_wavefront, logic_first, intake_ledger
from solver import m2c_adapter, m2c_input, target_intake, workspace


def select(conn, excluded, limit=None):
    rows = conn.execute("select f.name,f.insn_count,f.is_leaf,f.tu_id from functions f "
                        "where not exists(select 1 from attempts a where a.func_addr=f.addr) "
                        "order by f.addr").fetchall()
    chosen = []
    if limit is not None:
        if limit < 1:
            raise ValueError("intake limit must be positive")
        eligible = sorted((r for r in rows if r[0] not in excluded),
                          key=lambda r: (not r[2], r[1] if r[1] is not None else 10**9, r[0]))
        return [{"function": r[0], "instruction_count": r[1], "is_leaf": bool(r[2]),
                 "tu_id": r[3], "stratum": "all-sizes-leaf-first"} for r in eligible[:limit]]
    for leaf in (1, 0):
        for low, high in ((20, 80), (81, 200)):
            row = next((r for r in rows if r[0] not in excluded and r[2] == leaf
                        and r[1] is not None and low <= r[1] <= high), None)
            if row:
                chosen.append({"function": row[0], "instruction_count": row[1],
                               "is_leaf": bool(row[2]), "tu_id": row[3],
                               "stratum": f"{'leaf' if leaf else 'caller'}-{low}-{high}"})
    return chosen


def replay_selection(conn, path: Path, excluded):
    receipt = json.loads(path.read_text())
    if receipt.get("kind") not in ("fresh-gamewide-intake-probe", "gamewide-intake-replay"):
        raise ValueError("replay requires an intake probe receipt")
    selected = receipt["selection"]
    names = [row["function"] for row in selected]
    if not names or len(names) != len(set(names)) or set(names) & excluded:
        raise ValueError("empty, duplicate, or held-out replay selection")
    for row in selected:
        actual = conn.execute("select insn_count,is_leaf,tu_id from functions where name=?",
                              (row["function"],)).fetchall()
        if actual != [(row["instruction_count"], int(row["is_leaf"]), row["tu_id"])]:
            raise ValueError("replay inventory changed: " + row["function"])
    return selected


def intake_variants(conn, repo, name, asm, draft):
    return m2c_adapter.prioritized_variants(conn, repo, name, asm, draft)


def candidate_rank(attempt, source):
    # A failed compile has no byte score. Preserve declaration progress for the
    # next repair stage rather than always forwarding the original '?' draft.
    # This is an intake heuristic, never a semantic or byte-exactness metric.
    unknowns = len(re.findall(r"\bextern\s+\?|(?m:^[ \t]*\?\s+\w+\s*\()", source))
    return (attempt.exact, attempt.compiled, attempt.score,
            -unknowns if not attempt.compiled else 0)


def run(*, repo: Path, db: Path, project: Path, output: Path,
        replay: Path | None = None, limit: int | None = None):
    if output.exists():
        raise ValueError("refusing to overwrite a fresh-function probe")
    conn = sqlite3.connect(db, timeout=120)
    excluded = logic_first._heldout(project / "eval/sets")
    inventory = frozen_wavefront.code_paths(project)
    inventory += [repo / "tools/m2ctx.py", repo / "symbol_addrs.txt"]
    inventory += list((repo / "include").rglob("*.h"))
    # The new declaration feedback can inspect binary-extracted data tables.
    # Pin that input too, rather than calling only the Python source frozen.
    inventory += list((repo / "asm").rglob("*.s"))
    pins = frozen_wavefront.file_hashes(inventory)
    revision = hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()
    selected = (replay_selection(conn, replay, excluded) if replay else
                select(conn, excluded | intake_ledger.parked(conn, revision), limit))
    run_id = f"fresh-gamewide-probe-{time.time_ns()}"
    receipt = {"kind": "gamewide-intake-replay" if replay else "fresh-gamewide-intake-probe", "run_id": run_id,
               "status": "running", "selection": selected, "code_hashes": pins,
               "regime": ("same-cohort development replay; not fresh or held-out" if replay else
                          "previously unattempted DEV functions; not a held-out success rate"),
               "replay_source": ({"path": str(replay), "sha256": hashlib.sha256(replay.read_bytes()).hexdigest()}
                                 if replay else None),
               "m2c_context": "target assembly only; contextual bootstrap draft discarded",
               "compile_context": "existing compiler/project headers; not a binary-only environment",
               "model_tokens": 0, "nodes": [], "intake_revision": revision,
               "intake_limit": limit}
    frozen_wavefront.wave._atomic_json(output, receipt)
    roots = []
    try:
        for frozen in selected:
            frozen_wavefront.verify_files(pins)
            name = frozen["function"]
            if not replay and conn.execute("select 1 from attempts a join functions f on f.addr=a.func_addr "
                            "where f.name=? limit 1", (name,)).fetchone():
                raise ValueError("selected function acquired an attempt before its probe: " + name)
            try:
                ws = workspace.bootstrap(repo, name)
            except RuntimeError as exc:
                # An unsupported intake target belongs in the denominator.
                # Do not replace it with an easier function or abort siblings.
                status = ("assembly_backend_required" if isinstance(exc, target_intake.AssemblyBackendRequired)
                          else "target_extraction")
                receipt["nodes"].append({**frozen, "status": status,
                                         "error": str(exc)[:1500]})
                frozen_wavefront.wave._atomic_json(output, receipt)
                continue
            # Do not reuse base.c: the bootstrap tool can supply reconstructed
            # TU context. Invoke m2c independently with no --context argument.
            result, draft_input = m2c_input.draft(repo, ws / "target.s")
            row = {**frozen, "target_sha256": hashlib.sha256((ws / "target.s").read_bytes()).hexdigest(),
                   "m2c_returncode": result.returncode, "draft_input": draft_input, "attempts": []}
            resolution = ws / "target-resolution.json"
            if resolution.exists():
                row["target_resolution"] = json.loads(resolution.read_text())
            receipt["nodes"].append(row)
            if result.returncode:
                row.update(status="draft_failed", error=(result.stdout + result.stderr)[-2000:])
                frozen_wavefront.wave._atomic_json(output, receipt)
                continue
            draft = '#include "common.h"\n\n' + result.stdout
            variants = intake_variants(conn, repo, name, workspace.target_asm(ws, name), draft)
            best = None
            parent = None
            for index, variant in enumerate(variants):
                workspace.assert_uncontaminated(variant.source, repo, name)
                attempt = workspace.score(ws, repo, f"{name}_fresh_probe_{time.time_ns()}",
                    variant.source, conn=conn, func=name,
                    strategy=("m2c-project-header-intake" if "project" in variant.label else "fresh-binary-m2c-intake"),
                    model="", run_id=run_id, parent_attempt_id=parent,
                    relation="binary-declaration-adaptation", action=variant.label,
                    run_kind=receipt["kind"], run_config={"selection": selected,
                        "m2c_context": "assembly only", "max_variants": 4})
                if parent is None:
                    parent = attempt.receipt_id
                row["attempts"].append({"label": variant.label,
                                        "adaptation_evidence": variant.context_receipts,
                                        **asdict(attempt)})
                if best is None or candidate_rank(attempt, variant.source) > candidate_rank(*best):
                    best = (attempt, variant.source)
                if attempt.exact:
                    break
            attempt, source = best
            roots.append({**frozen, "attempt_id": attempt.receipt_id,
                          "source_sha256": hashlib.sha256(source.encode()).hexdigest()})
            row.update(status="compiled" if attempt.compiled else "compile_failed",
                       best_attempt_id=attempt.receipt_id, score=attempt.score, exact=attempt.exact)
            frozen_wavefront.wave._atomic_json(output, receipt)
        callees, _ = callgraph.edges(conn)
        names = {row["function"] for row in roots}
        manifest = {"kind": "logic-first-connected-dev-cluster", "cluster": roots,
                    "selection": {"source": receipt["regime"], "target_source_inspected": False},
                    "binary_call_edges": [{"caller": caller, "callee": callee}
                                          for caller in sorted(names)
                                          for callee in sorted(callees.get(caller, set()) & names)]}
        manifest["manifest_digest"] = logic_first._digest(manifest)
        manifest_path = output.with_name(output.stem + ".manifest.json")
        frozen_wavefront.wave._atomic_json(manifest_path, manifest)
        if roots:
            census = dag_pipeline_pilot.run(repo=repo, db=db, sets=project / "eval/sets",
                manifest_path=manifest_path, output=output.with_name(output.stem + ".census.json"),
                max_cases=512, max_steps=2000)
            receipt["census"] = {"aggregate": census["aggregate"], "nodes": [
                {"function": row["function"], "stop_stage": row["stop_stage"],
                 "stop_reason": row["stop_reason"]} for row in census["dag"]["nodes"]]}
        frozen_wavefront.verify_files(pins)
        receipt.update(status="complete", frozen_code_unchanged=True)
    except Exception as exc:
        receipt.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        # Even pre-compiler failures must consume their place in this revision's
        # queue. Refuse checkpoints from a code/input-changing experiment.
        try:
            frozen_wavefront.verify_files(pins)
            intake_ledger.record(conn, revision, output, receipt['nodes'])
        except frozen_wavefront.FrozenInputChanged:
            receipt['intake_checkpoint'] = 'declined: inputs changed'
        conn.close()
        frozen_wavefront.wave._atomic_json(output, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "db", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--replay", type=Path, help="Replay exactly the selection in an earlier intake receipt")
    parser.add_argument("--limit", type=int, help="select this many unattempted functions across all sizes")
    result = run(**vars(parser.parse_args()))
    print(json.dumps({key: result.get(key) for key in ("status", "error", "census")}, indent=2))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
