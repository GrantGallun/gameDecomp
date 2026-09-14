"""Zero-model compile recovery before wavefront semantic eligibility checks."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

from eval import dag_pipeline_pilot, logic_first
from solver import m2c_adapter, m2c_input, workspace


def _atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(*, repo: Path, db: Path, sets: Path, conn, node: dict, output: Path,
        max_cases: int = 512, max_steps: int = 2000) -> dict:
    if output.exists():
        raise ValueError("refusing to overwrite compile-intake receipt")
    started = time.monotonic()
    function = node["function"]
    receipt = {"kind": "wavefront-compile-intake", "function": function,
               "status": "running", "model_calls": 0, "max_variants": 4,
               "parent_attempt_id": node["attempt"]["attempt_id"], "attempts": []}
    def save():
        receipt["wall_seconds"] = round(time.monotonic() - started, 3)
        _atomic_json(output, receipt)
    save()
    try:
        ws = workspace.bootstrap(repo, function)
        result, draft_input = m2c_input.draft(repo, ws / "target.s")
        receipt["draft_input"] = draft_input
        if result.returncode:
            receipt.update(status="draft_failed", error=(result.stdout + result.stderr)[-2000:])
            save()
            return receipt
        draft = '#include "common.h"\n\n' + result.stdout
        variants = m2c_adapter.prioritized_variants(
            conn, repo, function, workspace.target_asm(ws, function), draft)
        best = None
        run_id = f"wavefront-compile-intake-{time.time_ns()}"
        receipt["run_id"] = run_id
        for index, variant in enumerate(variants):
            workspace.assert_uncontaminated(variant.source, repo, function)
            attempt = workspace.score(
                ws, repo, f"{function}_compile_intake_{time.time_ns()}", variant.source,
                conn=conn, func=function, model="", run_id=run_id,
                strategy=("compile-intake:project-header" if "project" in variant.label else "compile-intake:binary"),
                iteration=index, parent_attempt_id=receipt["parent_attempt_id"],
                relation="compile-intake-redraft", action=variant.label,
                run_kind=receipt["kind"], run_config={"max_variants": 4, "model_calls": 0},
                extra={"context_receipts": variant.context_receipts})
            receipt["attempts"].append({"label": variant.label, **asdict(attempt)})
            save()
            if attempt.compiled and (best is None or (attempt.exact, attempt.score) >
                                      (best[0].exact, best[0].score)):
                best = (attempt, variant.source)
            if attempt.exact:
                break
        if best is None:
            receipt["status"] = "compile_failed"
        else:
            attempt, source = best
            root = {"function": function, "attempt_id": attempt.receipt_id,
                    "source_sha256": hashlib.sha256(source.encode()).hexdigest()}
            manifest = {"kind": "logic-first-connected-dev-cluster", "cluster": [root],
                        "binary_call_edges": [], "selection": {"source": "wavefront compile repair",
                                                                 "target_source_inspected": False}}
            manifest["manifest_digest"] = logic_first._digest(manifest)
            manifest_path = output.with_name(output.stem + ".manifest.json")
            _atomic_json(manifest_path, manifest)
            census_path = output.with_name(output.stem + ".census.json")
            census = dag_pipeline_pilot.run(repo=repo, db=db, sets=sets,
                manifest_path=manifest_path, output=census_path,
                max_cases=max_cases, max_steps=max_steps)
            fresh = census["dag"]["nodes"][0]
            # The local recensus cannot redefine the original wave's DAG edges.
            for key in ("dag_level", "dependency_component", "panel_dependencies"):
                if key in node:
                    fresh[key] = node[key]
            receipt.update(status="compiled", node=fresh, census=str(census_path),
                           best_attempt_id=attempt.receipt_id, score=attempt.score,
                           exact=attempt.exact)
    except Exception as exc:
        receipt.update(status="error", error=f"{type(exc).__name__}: {exc}")
    save()
    return receipt
