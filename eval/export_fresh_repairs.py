"""Export fresh, receipted repair examples from a collection run's scratch database.

WHY THE PAIRS HERE LOOK DIFFERENT FROM THE KNOWLEDGE BASE'S
-----------------------------------------------------------
The obvious pairing -- "the repair attempt that beat its parent" -- produced nothing in the
2026-09-20 pilot: 16 repair draws, none improving, so a dataset built that way is empty. But
the absence of an improving REPAIR is not the absence of an improving PAIR. Each function's
independent draws span a wide score range (`insertHuffmanQueueNode` produced candidates from
0.00 to 50.98 in four draws), and any two of them give a real state and a real, higher-scoring
target for the same function, both compiled by the oracle, both with full receipts.

So the rule here is:

    parent  = a compiled candidate that is NOT the function's best
    child   = the function's best compiled candidate, when it beats the parent by epsilon

and the pair is labelled `observed-same-run` rather than `observed-repair`, because the model
did not produce the child FROM the parent. Calling this a refinement trajectory would repeat
the exact defect the September 19 review found -- a relationship the model was never shown,
recorded as though it had been.

WHAT IS STRONGER HERE THAN THE KB EXPORT
----------------------------------------
The repaired state is RE-RENDERED from the recorded parent C and the recorded compiler outcome
through the same `render_repair_prompt` the runtime uses, so the training input is a real
function of two recorded fields rather than an unverifiable reconstruction. Every parent row
also carries the prompt it was actually generated from, so the exporter can report, per record,
whether the rendered prompt matches a prompt the run really used.

WHAT IT REFUSES TO DO
---------------------
- It never pairs across functions.
- It never uses a candidate that failed to compile as a parent or a child: a non-compiling
  parent makes the lesson "write something that compiles", which is not the repair task.
- It never uses a child that is not byte-exact OR strictly higher-scoring.
- It never mixes in a row from an archived batch: the caller names the database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROVENANCE_SAME_RUN = "observed-same-run"
PROVENANCE_REPAIR = "observed-repair"


def _repair_prompt(target_asm: str, candidate_c: str, compiled: bool, score: float,
                   diff: str, stderr: str) -> str:
    from eval.trajectory_factory import RepairState, render_repair_prompt
    state = RepairState(source=candidate_c, compiled=bool(compiled), score=float(score or 0.0),
                        diff=diff or "", compiler_stderr=stderr or "")
    return render_repair_prompt(state, target_asm)


def export(db: Path, out: Path, *, target_asm_root: Path, epsilon: float = 0.5,
           require_prompt_match: bool = False, game: str = "sbk1") -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    from eval import seal
    sealed = seal.sealed_in_sets()
    functions = {row[0]: row[1] for row in
                 conn.execute("select addr, name from functions") if row[1] not in sealed}
    records: list[dict] = []
    audit = {"candidates": 0, "functions": 0, "skipped_no_asm": 0,
             "skipped_no_compile": 0, "skipped_no_improvement": 0,
             "repair_children_used": 0, "independent_children_used": 0,
             "prompt_matched_a_real_prompt": 0}

    rows = conn.execute("""
        select a.id, a.func_addr, a.source_code, a.compiled, a.score, a.diff_summary,
               a.compiler_stderr, a.prompt_context, a.parent_attempt_id, a.sampling,
               a.model
        from attempts a order by a.func_addr, a.score""").fetchall()
    by_function: dict[int, list] = {}
    for row in rows:
        if row[1] in functions:                       # sealed functions' attempts never become training pairs
            by_function.setdefault(row[1], []).append(row)
    audit["candidates"] = len(rows)

    from solver import signals

    for addr, group in sorted(by_function.items()):
        name = functions.get(addr)
        if not name:
            continue
        audit["functions"] += 1
        asm = read_target_asm(target_asm_root, name)
        if not asm:
            audit["skipped_no_asm"] += 1
            continue
        compiled = [row for row in group if row[3]]
        if len(compiled) < 2:
            audit["skipped_no_compile"] += 1
            continue
        best = max(compiled, key=lambda row: row[4] or 0.0)
        for parent in compiled:
            if parent[0] == best[0]:
                continue
            delta = (best[4] or 0.0) - (parent[4] or 0.0)
            if delta <= epsilon:
                audit["skipped_no_improvement"] += 1
                continue
            prompt = _repair_prompt(asm, parent[2] or "", bool(parent[3]), parent[4] or 0.0,
                                    parent[5] or "", parent[6] or "")
            matched = bool(parent[7]) and parent[7] == prompt
            if matched:
                audit["prompt_matched_a_real_prompt"] += 1
            if require_prompt_match and not matched:
                continue
            sampling = json.loads(parent[9] or "{}")
            role = (sampling.get("generation", {}).get("sampling", {}) or {}).get("role", "")
            # A child that the run produced AS A REPAIR of this parent is the stronger tier.
            child_is_repair = (best[8] == parent[0])
            provenance = PROVENANCE_REPAIR if child_is_repair else PROVENANCE_SAME_RUN
            if child_is_repair:
                audit["repair_children_used"] += 1
            else:
                audit["independent_children_used"] += 1
            records.append({
                "id": f"{game}:{name}:{parent[0]}->{best[0]}",
                "game": game, "function": name, "func_addr": int(addr), "tu": None,
                "provenance": provenance,
                "lineage": "observed" if child_is_repair else "same-run-independent",
                "split": "train",
                "input": {
                    "target_asm": asm,
                    "target_asm_available": True,
                    "compiler": "ido-5.3",
                    "flags": None,
                    "declarations": None,
                    "candidate_c": parent[2] or "",
                    "compiler_outcome": {
                        "compiled": bool(parent[3]), "score": parent[4], "exact": False,
                        "diff": parent[5] or "", "stderr": parent[6] or ""},
                },
                "target": {"source_c": best[2] or "", "exact": False, "score": best[4]},
                "meta": {
                    "parent_attempt_id": parent[0], "child_attempt_id": best[0],
                    "parent_sha256": hashlib.sha256((parent[2] or "").encode()).hexdigest(),
                    "child_sha256": hashlib.sha256((best[2] or "").encode()).hexdigest(),
                    "score_delta": round(delta, 6),
                    "parent_run_role": role,
                    "prompt_is_a_prompt_the_run_used": matched,
                    "lineage_note": (
                        "the child is this run's best compiled candidate for the function and "
                        "the parent is another compiled candidate from the same run; the model "
                        "was not shown the parent when it produced the child unless "
                        "parent_run_role == 'repair'"),
                    "generator_model": parent[10] or "",
                    "child_model": best[10] or "",
                    "source": "fresh collection pilot, scratch attempt DB",
                },
            })

    out.mkdir(parents=True, exist_ok=True)
    jsonl = out / "repair_dataset.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    audit["records"] = len(records)
    audit["records_by_provenance"] = {}
    for record in records:
        key = record["provenance"]
        audit["records_by_provenance"][key] = audit["records_by_provenance"].get(key, 0) + 1
    audit["distinct_functions"] = len({r["function"] for r in records})
    audit["largest_function_share"] = (
        round(max((sum(1 for r in records if r["function"] == f) for f in
                   {r["function"] for r in records}), default=0)
              / max(len(records), 1), 4))
    audit["jsonl_sha256"] = hashlib.sha256(jsonl.read_bytes()).hexdigest()
    (out / "AUDIT.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


def read_target_asm(root: Path, function: str) -> str | None:
    """The same glabel/endlabel body `solver.workspace.target_asm` extracts."""
    path = Path(root) / function / "target.s"
    if not path.exists():
        return None
    text = path.read_text(errors="replace")
    start = text.find(f"glabel {function}")
    if start == -1:
        return None
    end = text.find(f"endlabel {function}")
    return text[start:end if end != -1 else None].strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target-asm-root", type=Path,
                    default=Path.home() / "decomp" / "sbk1" / "nonmatchings")
    ap.add_argument("--epsilon", type=float, default=0.5)
    ap.add_argument("--require-prompt-match", action="store_true")
    args = ap.parse_args(argv)
    audit = export(args.db, args.out, target_asm_root=args.target_asm_root,
                   epsilon=args.epsilon, require_prompt_match=args.require_prompt_match)
    print(json.dumps(audit, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
