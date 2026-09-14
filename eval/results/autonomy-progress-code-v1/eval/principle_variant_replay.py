"""Replay deterministic isolated-register-web C variants on a frozen panel."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from eval import agentrepair
from solver import principle_variants, principles, refine, residual, workspace


PRINCIPLE_ID = "isolated-register-web-source-shape"


def _manifest_digest(value: dict) -> str:
    unsigned = dict(value)
    unsigned.pop("manifest_digest", None)
    return hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _summary(attempt: workspace.Attempt,
             packet: residual.ResidualPacket) -> dict:
    return {
        "attempt_id": attempt.receipt_id,
        "compiled": attempt.compiled,
        "exact": attempt.exact,
        "weighted_progress_score": attempt.score,
        "residual": packet.to_dict(),
    }


def _rank(attempt: workspace.Attempt,
          packet: residual.ResidualPacket) -> tuple:
    if attempt.exact:
        return (0,)
    if not attempt.compiled:
        return (2, len(attempt.compiler_stderr))
    distance = (packet.positional_byte_distance
                if packet.positional_byte_distance is not None else 10**9)
    return (1, packet.faults["structural"],
            abs(packet.instruction_delta or 0), sum(packet.faults.values()),
            distance, -attempt.score)


def _aggregate(rows: list[dict]) -> dict:
    completed = [row for row in rows if "root" in row and "best" in row]
    return {
        "applicable_functions": len(completed),
        "exact": sum(bool(row["best"]["exact"]) for row in completed),
        "improved_byte_distance": sum(
            row["best"]["residual"]["positional_byte_distance"] is not None
            and row["root"]["residual"]["positional_byte_distance"] is not None
            and row["best"]["residual"]["positional_byte_distance"]
            < row["root"]["residual"]["positional_byte_distance"]
            for row in completed),
        "compiled_variants": sum(row["compiled_variants"] for row in completed),
        "attempted_variants": sum(row["attempted_variants"] for row in completed),
    }


def run(*, repo: Path, db: Path, sets: Path, manifest_path: Path,
        out: Path, max_variants: int = 32,
        max_minutes: float = 20.0, verbose: bool = False) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("manifest_digest") != _manifest_digest(manifest):
        raise ValueError("panel manifest digest is invalid")
    panel = [row for row in manifest.get("panel", [])
             if PRINCIPLE_ID in row.get("principle_ids", [])]
    if not panel:
        raise ValueError(f"no panel function activates {PRINCIPLE_ID}")
    for row in panel:
        agentrepair._refuse_frozen_heldout(sets, row["function"])

    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    run_id = f"principle-variant-{time.time_ns()}"
    config = {
        "schema_version": 1,
        "kind": "deterministic-principle-variant-replay",
        "principle_id": PRINCIPLE_ID,
        "manifest": str(manifest_path),
        "manifest_digest": manifest["manifest_digest"],
        "max_variants_per_function": max_variants,
        "max_minutes": max_minutes,
        "terminal_success": "verifier exact=true only",
        "target_reference_c_available": False,
        "family_frozen_before_compile": True,
    }
    receipt = {
        "schema_version": 1,
        "kind": config["kind"],
        "run_id": run_id,
        "created_at": int(time.time()),
        "config": config,
        "functions": [],
        "stopped_for_time": False,
    }
    started = time.monotonic()

    for function_index, panel_row in enumerate(panel):
        if time.monotonic() - started >= max_minutes * 60:
            receipt["stopped_for_time"] = True
            break
        name = panel_row["function"]
        source_attempt_id = int(panel_row["attempt_id"])
        source = agentrepair._source_for_attempt(conn, source_attempt_id, name)
        if hashlib.sha256(source.encode()).hexdigest() != panel_row["source_sha256"]:
            raise ValueError(f"frozen source hash changed for {name}")
        ws = workspace.bootstrap(repo, name)
        target_asm = workspace.target_asm(ws, name)
        root_tag = f"{name}_principle_variant_root_{time.time_ns()}"
        root_attempt = workspace.score(
            ws, repo, root_tag, source, conn=conn, func=name,
            strategy="principle-variant-root-reverify", model="deterministic",
            run_id=run_id, iteration=0,
            parent_attempt_id=source_attempt_id,
            relation="principle-variant-root",
            action="fresh shared root verification", run_kind=config["kind"],
            run_config=config)
        root_object = ws / f"{root_tag}.o" if root_attempt.compiled else None
        root_packet = residual.build(
            root_attempt, target_asm=target_asm,
            target_object=ws / "target.o", candidate_object=root_object)
        activated = {
            item.pattern_id for item in principles.retrieve(
                target_asm, root_attempt, root_packet,
                include_hypotheses=True)
        }
        row = {
            "function": name,
            "source_attempt_id": source_attempt_id,
            "root": _summary(root_attempt, root_packet),
            "principle_reactivated": PRINCIPLE_ID in activated,
            "generated_variants": 0,
            "attempted_variants": 0,
            "compiled_variants": 0,
            "variants": [],
        }
        if root_attempt.exact:
            row["excluded"] = "fresh root became exact"
            row["best"] = row["root"]
            receipt["functions"].append(row)
            continue
        if PRINCIPLE_ID not in activated:
            row["excluded"] = "principle did not reactivate on fresh root"
            row["best"] = row["root"]
            receipt["functions"].append(row)
            continue

        variants = principle_variants.isolated_register_web(
            source, name, max_variants=max_variants)
        row["generated_variants"] = len(variants)
        best_attempt, best_packet = root_attempt, root_packet
        for variant_index, variant in enumerate(variants, start=1):
            if time.monotonic() - started >= max_minutes * 60:
                receipt["stopped_for_time"] = True
                break
            tag = f"{name}_principle_variant_{variant_index}_{time.time_ns()}"
            attempt = workspace.score(
                ws, repo, tag, variant.source, conn=conn, func=name,
                strategy="principle-variant-isolated-register-web",
                model="deterministic", run_id=run_id,
                iteration=variant_index,
                parent_attempt_id=root_attempt.receipt_id,
                relation="principle-source-shape-variant",
                action=variant.label, run_kind=config["kind"],
                run_config=config, extra={"variant_label": variant.label})
            object_path = ws / f"{tag}.o" if attempt.compiled else None
            packet = residual.build(
                attempt, target_asm=target_asm,
                target_object=ws / "target.o", candidate_object=object_path)
            variant_row = {
                "label": variant.label,
                "source_sha256": hashlib.sha256(
                    variant.source.encode()).hexdigest(),
                **_summary(attempt, packet),
            }
            row["variants"].append(variant_row)
            row["attempted_variants"] += 1
            row["compiled_variants"] += int(attempt.compiled)
            if _rank(attempt, packet) < _rank(best_attempt, best_packet):
                best_attempt, best_packet = attempt, packet
            if verbose:
                print(json.dumps({
                    "function": name, "variant": variant.label,
                    "compiled": attempt.compiled, "exact": attempt.exact,
                    "bytes": packet.positional_byte_distance,
                }), flush=True)
            if attempt.exact:
                break
            receipt["aggregate"] = _aggregate(
                receipt["functions"] + [{**row, "best": _summary(
                    best_attempt, best_packet)}])
            agentrepair._atomic_json(out, receipt)
        row["best"] = _summary(best_attempt, best_packet)
        row["wall_seconds"] = time.monotonic() - started
        receipt["functions"].append(row)
        receipt["aggregate"] = _aggregate(receipt["functions"])
        agentrepair._atomic_json(out, receipt)
        if receipt["stopped_for_time"]:
            break

    receipt["elapsed_seconds"] = time.monotonic() - started
    receipt["completed_functions"] = len(receipt["functions"])
    receipt["aggregate"] = _aggregate(receipt["functions"])
    agentrepair._atomic_json(out, receipt)
    conn.close()
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--sets", type=Path, default=Path("eval/sets"))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-variants", type=int, default=32)
    parser.add_argument("--max-minutes", type=float, default=20.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    result = run(
        repo=args.repo.expanduser().resolve(), db=args.db.expanduser().resolve(),
        sets=args.sets.expanduser().resolve(),
        manifest_path=args.manifest.expanduser().resolve(),
        out=args.out.expanduser().resolve(), max_variants=args.max_variants,
        max_minutes=args.max_minutes, verbose=args.verbose)
    print(json.dumps({
        "completed_functions": result["completed_functions"],
        "stopped_for_time": result["stopped_for_time"],
        "elapsed_seconds": result["elapsed_seconds"],
        "aggregate": result["aggregate"],
    }, indent=2))


if __name__ == "__main__":
    main()
