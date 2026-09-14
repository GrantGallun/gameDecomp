"""Create source-blind receipts for public N64 provenance hypotheses.

The receipt records repository identity, indexed source location, compiled
artifact hashes, and transparent Ghidra comparison metrics.  It never stores a
source body and never promotes a hypothesis to a match.  Promotion belongs to
the target compiler plus byte-exact oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from solver import ghidra_context, ghidra_similarity
from tools import n64_corpus, relocated_oracle


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _repo_record(index: Path, repo_id: str) -> dict[str, object]:
    stats = n64_corpus.index_stats(index)
    matches = [repo for repo in stats["repositories"]
               if repo["id"] == repo_id]
    if len(matches) != 1:
        raise ValueError(f"corpus index has no unique repository {repo_id!r}")
    return matches[0]


def build_hypothesis(
        *, index: Path, target_evidence: Path, candidate_evidence: Path,
        target_function: str, repo_id: str, source_path: str,
        source_function: str, compiled_artifact: Path,
        discovery_similarity: float | None = None,
        oracle_receipt: Path | None = None,
        history: dict[str, object] | None = None,
        ) -> dict[str, object]:
    """Build one provenance receipt without loading public source text."""
    symbol_hits = n64_corpus.query_symbol(index, source_function)
    locations = [hit for hit in symbol_hits
                 if hit["repo_id"] == repo_id and hit["path"] == source_path]
    if len(locations) != 1:
        raise ValueError(
            f"index has no unique {repo_id}:{source_path}:{source_function} hit")
    location = locations[0]
    repository = _repo_record(index, repo_id)
    if location["head"] != repository["head"]:
        raise ValueError("symbol and repository index heads disagree")

    target = ghidra_context.load(target_evidence)
    candidate = ghidra_context.load(candidate_evidence)
    comparison = ghidra_similarity.compare(target, candidate)
    artifact_size = compiled_artifact.stat().st_size
    identity = {
        "target_function": target_function,
        "repo_id": repo_id,
        "repo_head": repository["head"],
        "source_path": source_path,
        "source_function": source_function,
        "compiled_sha256": _digest(compiled_artifact),
        "target_evidence_sha256": _digest(target_evidence),
        "candidate_evidence_sha256": _digest(candidate_evidence),
    }
    hypothesis_id = hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    discovery: dict[str, object] = {
        "exact_symbol_hit": source_function == target_function,
        "indexed_normalized_sha256": location["normalized_sha256"],
        "indexed_token_count": location["token_count"],
    }
    if discovery_similarity is not None:
        if not 0.0 <= discovery_similarity <= 1.0:
            raise ValueError("discovery similarity must be between zero and one")
        discovery["source_sketch_similarity"] = discovery_similarity

    promotion: dict[str, object] = {
        "target_candidate_compiled": False,
        "target_oracle_tested": False,
        "target_oracle_exact": False,
        "status": "provenance_supported_not_promoted",
        "requirement": "compile as target candidate plus byte-exact oracle",
    }
    if oracle_receipt is not None:
        oracle = json.loads(oracle_receipt.read_text(encoding="utf-8"))
        relocated_oracle.validate_exact_receipt(oracle)
        exact = bool(oracle["exact"])
        candidate = oracle.get("candidate")
        if not isinstance(candidate, dict) or candidate.get(
                "sha256") != identity["compiled_sha256"]:
            raise ValueError("oracle receipt does not bind the compiled artifact")
        promotion = {
            "target_candidate_compiled": oracle["compiled"],
            "target_oracle_tested": oracle["oracle_tested"],
            "target_oracle_exact": exact,
            "status": ("oracle_promoted_relocated_exact" if exact else
                       "target_oracle_rejected"),
            "requirement": "compile as target candidate plus byte-exact oracle",
            "oracle_receipt": _portable(oracle_receipt),
            "oracle_receipt_sha256": _digest(oracle_receipt),
            "oracle_kind": oracle["kind"],
            "oracle_status": oracle.get("status"),
            "candidate_sha256": oracle.get("candidate", {}).get("sha256")
                if isinstance(oracle.get("candidate"), dict) else None,
            "relocated_text": oracle.get("relocated_text"),
            "data_sections": oracle.get("data_sections"),
        }

    result = {
        "hypothesis_id": hypothesis_id,
        "target_function": target_function,
        "corpus_source": {
            "repo_id": repo_id,
            "repo_head": repository["head"],
            "evaluation_policy": repository["evaluation_policy"],
            "path": source_path,
            "line": location["line"],
            "function": source_function,
            "source_body_stored": False,
        },
        "discovery": discovery,
        "compiled_artifact": {
            "path": _portable(compiled_artifact),
            "sha256": identity["compiled_sha256"],
            "bytes": artifact_size,
            "compiled": True,
        },
        "ghidra": {
            "target_evidence": _portable(target_evidence),
            "target_evidence_sha256": identity["target_evidence_sha256"],
            "candidate_evidence": _portable(candidate_evidence),
            "candidate_evidence_sha256": identity["candidate_evidence_sha256"],
            "comparison": comparison,
        },
        "promotion": promotion,
    }
    if history is not None:
        result["history"] = history
    return result


def load_history(path: Path, target_function: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get(
            "kind") != "n64_shared_function_history":
        raise ValueError("unsupported shared-function history")
    records = payload.get("records")
    if not isinstance(records, dict) or not isinstance(
            records.get(target_function), dict):
        raise ValueError(f"history has no record for {target_function}")
    record = records[target_function]
    for branch in ("public_history", "target_history"):
        if not isinstance(record.get(branch), list):
            raise ValueError(f"history record has invalid {branch}")
    if not isinstance(record.get("interpretation"), str):
        raise ValueError("history record has no bounded interpretation")
    return record


def update_registry(path: Path, hypothesis: dict[str, object], *,
                    index: Path) -> dict[str, object]:
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION or payload.get(
                "kind") != "n64_public_provenance_hypotheses":
            raise ValueError("unsupported provenance registry")
        hypotheses = payload.get("hypotheses")
        if not isinstance(hypotheses, list):
            raise ValueError("provenance registry has invalid hypotheses")
    else:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "n64_public_provenance_hypotheses",
            "policy": {
                "source_bodies_stored": False,
                "source_in_solver_prompt": False,
                "role": "ranking and provenance hypothesis only",
                "promotion": "target compile plus byte-exact oracle",
            },
            "hypotheses": [],
        }
        hypotheses = payload["hypotheses"]

    hypothesis_id = hypothesis["hypothesis_id"]
    hypotheses[:] = [row for row in hypotheses
                     if row.get("hypothesis_id") != hypothesis_id]
    hypotheses.append(hypothesis)
    hypotheses.sort(key=lambda row: (
        str(row.get("target_function", "")), str(row.get("hypothesis_id", ""))))
    payload["corpus_index"] = {
        "path": _portable(index),
        "sha256": _digest(index),
        "stats": n64_corpus.index_stats(index),
    }
    return payload


def write_registry(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=n64_corpus.DEFAULT_INDEX)
    parser.add_argument("--target-evidence", type=Path, required=True)
    parser.add_argument("--candidate-evidence", type=Path, required=True)
    parser.add_argument("--target-function", required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--source-function", required=True)
    parser.add_argument("--compiled-artifact", type=Path, required=True)
    parser.add_argument("--discovery-similarity", type=float)
    parser.add_argument(
        "--oracle-receipt", type=Path,
        help="optional relocated-byte oracle receipt for guarded promotion")
    parser.add_argument(
        "--history", type=Path,
        help="optional source-blind commit-history record")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    index = args.index.resolve()
    hypothesis = build_hypothesis(
        index=index,
        target_evidence=args.target_evidence.resolve(),
        candidate_evidence=args.candidate_evidence.resolve(),
        target_function=args.target_function,
        repo_id=args.repo_id,
        source_path=args.source_path,
        source_function=args.source_function,
        compiled_artifact=args.compiled_artifact.resolve(),
        discovery_similarity=args.discovery_similarity,
        oracle_receipt=(args.oracle_receipt.resolve()
                        if args.oracle_receipt is not None else None),
        history=(load_history(args.history.resolve(), args.target_function)
                 if args.history is not None else None),
    )
    payload = update_registry(args.out.resolve(), hypothesis, index=index)
    write_registry(args.out.resolve(), payload)
    print(json.dumps(hypothesis, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
