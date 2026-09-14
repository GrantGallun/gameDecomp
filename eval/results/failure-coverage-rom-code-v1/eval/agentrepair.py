"""Run the minimal compile/residual/hypothesis/patch repair loop.

This is intentionally a thin executable around :mod:`solver.modelrepair`.
It recompiles the selected root, records an append-only verifier receipt,
feeds exactness-first residual packets to a provider-neutral search kernel,
and writes both the best source and a machine-readable run receipt.

Example (run from the workbench repository under WSL)::

    python3 -m eval.agentrepair \
      --repo ~/decomp/sbk1 --db ~/decomp/kb-sbk1.sqlite \
      --function randomNextObject --attempt-id 1234 \
      --out eval/results/agentrepair-randomNextObject.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

from solver import diagnose, llm, modelrepair, refine, residual, workspace, repair as deterministic


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _source_for_attempt(conn: sqlite3.Connection, attempt_id: int,
                        function: str) -> str:
    row = conn.execute(
        "SELECT f.name, a.source_code FROM attempts a "
        "JOIN functions f ON f.addr=a.func_addr WHERE a.id=?",
        (attempt_id,)).fetchone()
    if row is None:
        raise ValueError(f"attempt {attempt_id} does not exist")
    if row[0] != function:
        raise ValueError(
            f"attempt {attempt_id} belongs to {row[0]}, not {function}")
    return str(row[1])


def _load_provider(spec: str) -> modelrepair.ProposalProvider:
    """Load a no-argument provider class/factory as ``module:attribute``."""
    if ":" not in spec:
        raise ValueError("provider must use module:attribute syntax")
    module_name, attribute = spec.rsplit(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    provider = factory()
    if not isinstance(getattr(provider, "provider_id", None), str) \
            or not callable(getattr(provider, "generate", None)):
        raise TypeError(
            "provider must expose string provider_id and generate(request)")
    return provider


def _refuse_frozen_heldout(set_path: Path | None, function: str) -> None:
    if set_path is None or not set_path.exists():
        return
    paths = sorted(set_path.glob("*.json")) if set_path.is_dir() else [set_path]
    heldout: set[str] = set()
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        heldout.update(row["function"] for row in value.get("heldout", [])
                       if isinstance(row, dict)
                       and isinstance(row.get("function"), str))
    if function in heldout:
        raise ValueError(
            f"{function} is in the frozen held-out split; interactive repair "
            "is refused because it would tune directly against that target")


def _diagnosis(repo: Path, ws: Path, object_path: Path,
               target_asm: str) -> str:
    if not object_path.is_file():
        return ""
    result = diagnose.run(repo, ws / "target.o", object_path)
    return diagnose.prompt_block(repo, result, asm_len=len(target_asm)) \
        if result else ""


def run(*, repo: Path, db: Path, function: str, source: str,
        source_parent_attempt_id: int | None, out: Path, best_source_out: Path,
        model: str, endpoint: str, draws: int, depth: int, beam: int,
        max_calls: int, timeout: int, think: str, num_thread: int,
        temperature: float, num_predict: int, seed: int | None,
        cache_dir: Path | None, verbose: bool,
        provider: modelrepair.ProposalProvider | None = None,
        retained_frontier: tuple[dict, ...] = (), deterministic_budget: int = 0,
        deterministic_depth: int = 2, strategy_brief: str = "",
        structured_output: bool = False, retry_invalid: bool = False,
        include_header_context: bool = False, compile_only: bool = False,
        type_transaction: bool = False, resilient: bool = False,
        semantic_cases: int = 64, semantic_steps: int = 10000) -> dict:
    provider = provider or modelrepair.OllamaProvider()
    conn = sqlite3.connect(db, timeout=120)
    conn.execute("PRAGMA busy_timeout = 120000")
    refine.ensure_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM functions WHERE name=?", (function,)).fetchone()
    if row is None:
        raise ValueError(f"unknown function: {function}")

    ws = workspace.bootstrap(repo, function)
    target_asm = workspace.target_asm(ws, function)
    run_id = f"agentrepair-{time.time_ns()}-{function}"
    config = {
        "schema_version": 1,
        "kind": "interactive-residual-repair",
        "function": function,
        "model": model,
        "provider": provider.provider_id,
        "draws_per_parent": draws,
        "max_depth": depth,
        "beam_width": beam,
        "max_model_calls": max_calls,
        "timeout": timeout,
        "think": think,
        "num_thread": num_thread,
        "temperature": temperature,
        "num_predict": num_predict,
        "seed": seed,
        "cache_dir": str(cache_dir) if cache_dir else None,
        "terminal_success": "object verifier exact=true and configured candidate frontend passed; not whole-ROM proof",
        "deterministic_candidate_budget": deterministic_budget,
        "deterministic_depth": deterministic_depth,
        "strategy_brief": strategy_brief, "structured_output": structured_output,
        "retry_invalid": retry_invalid, "include_header_context": include_header_context,
        "compile_only": compile_only,
        "type_transaction": type_transaction,
        "resilient":resilient, "semantic_cases":semantic_cases,"semantic_steps":semantic_steps,
        "constraint_plan_budget":8 if resilient else 0,
    }

    root_tag = f"{function}_agentrepair_root_{time.time_ns()}"
    root = workspace.score(
        ws, repo, root_tag, source, conn=conn, func=function, iteration=0,
        strategy="agentrepair-root-reverify", model=model, run_id=run_id,
        parent_attempt_id=source_parent_attempt_id,
        relation="agent-repair-root", action="fresh root verification",
        run_kind="agent-repair", run_config=config)
    root_object = ws / f"{root_tag}.o" if root.compiled else None
    root_packet = residual.build(
        root, target_asm=target_asm, target_object=ws / "target.o",
        candidate_object=root_object)
    diagnosis = (_diagnosis(repo, ws, root_object, target_asm)
                 if root_object else "")

    initial_states = []
    deterministic_log = []
    context_reports = []
    if resilient:
        from solver import repair_context
        adapted, report = repair_context.project(repo,function,source,(root.compiler_recipe or {}).get('target',''))
        context_reports.append(report)
        if adapted != source:
            tag = f'{function}_context_{time.time_ns()}'
            att = workspace.score(ws,repo,tag,adapted,conn=conn,func=function,
                strategy='agentrepair-context-projection',model='zero-model',run_id=run_id,
                parent_attempt_id=root.receipt_id,relation='build-context-projection',
                action='metadata-only includes/ABI/scoped diagnostics',extra={'context_projection':report},
                run_kind='agent-repair',run_config=config)
            initial_states.append(modelrepair.CandidateState(adapted,att,ws/(tag+'.o') if att.compiled else None,
                ('recovered build context',),('context',)))
        from solver import type_constraints, type_plan, type_transaction as transaction
        seed_state=initial_states[-1] if initial_states else modelrepair.CandidateState(source,root,root_object)
        if not seed_state.attempt.compiled and type_plan.inventory(seed_state.source):
            try:
                measured=type_constraints.measure(repo,ws,seed_state.source,function,(root.compiler_recipe or {}).get('target',''))
                constrained=type_constraints.solve(seed_state.source,function,target_asm,measured['layouts'],max_plans=8)
                constrained['layout_probe_object_sha256']=measured['probe_object_sha256']
                constrained['layout_receipt_path']=measured['receipt_path']
                context_reports.append(constrained)
                abi=transaction.contract(repo,seed_state.source,function)
                for index,plan in enumerate(constrained['plans']):
                    try:
                        candidate,plan_report=type_plan.apply(repo,ws,seed_state.source,plan,function,abi,(root.compiler_recipe or {}).get('target',''))
                    except ValueError as exc:
                        context_reports.append({'stage':'constraint-plan','index':index,'status':'rejected','reason':str(exc)})
                        continue
                    tag=f'{function}_constraint_{time.time_ns()}_{index}'
                    att=workspace.score(ws,repo,tag,candidate,conn=conn,func=function,
                        strategy='agentrepair-type-constraints',model='zero-model',run_id=run_id,
                        parent_attempt_id=seed_state.attempt.receipt_id,relation='type-constraint-plan',
                        action='compiler-measured fields plus draft pointer-flow candidate',
                        extra={'type_plan':plan_report,'layout_object_sha256':measured['probe_object_sha256']},
                        run_kind='agent-repair',run_config=config)
                    initial_states.append(modelrepair.CandidateState(candidate,att,ws/(tag+'.o') if att.compiled else None,
                        seed_state.labels+('constraint-selected types',),seed_state.kinds+('type-constraints',)))
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                context_reports.append({'stage':'type-constraints','status':'unavailable','reason':str(exc)})
    if deterministic_budget > 0 and root.compiled and not root.exact:
        att, candidate, deterministic_log = deterministic.search(
            repo, function, source, ws, conn=conn, max_pairs=deterministic_budget,
            max_depth=deterministic_depth, beam_width=beam, parent_attempt_id=root.receipt_id,
            run_id=run_id, verbose=verbose)
        if candidate != source:
            tag = f"{function}_agentrepair_deterministic_{time.time_ns()}"
            verified = workspace.score(ws, repo, tag, candidate, conn=conn, func=function,
                strategy="agentrepair-deterministic-reverify", model=model, run_id=run_id,
                parent_attempt_id=att.receipt_id, relation="agent-repair-root",
                action="reverify deterministic seed", run_kind="agent-repair", run_config=config)
            initial_states.append(modelrepair.CandidateState(
                candidate, verified, ws / f"{tag}.o" if verified.compiled else None,
                ("deterministic compiler search",), ("deterministic",)))
    # Resilient handoff reserves room for semantic/byte champions plus intact
    # recovery state, even with a narrow search beam. Reverify, never trust old
    # scores or semantic reports across jobs.
    for index, item in enumerate(retained_frontier[:max(3 if resilient else 0, beam - 1)]):
        retained_id = int(item["attempt_id"])
        retained_source = _source_for_attempt(conn, retained_id, function)
        if hashlib.sha256(retained_source.encode()).hexdigest() != item["source_sha256"]:
            raise ValueError("retained frontier source identity changed")
        if retained_source == source:
            continue
        tag = f"{function}_agentrepair_retained_{time.time_ns()}_{index}"
        att = workspace.score(ws, repo, tag, retained_source, conn=conn, func=function,
            strategy="agentrepair-frontier-reverify", model=model, run_id=run_id,
            parent_attempt_id=retained_id, relation="agent-repair-root",
            action="reverify retained alternative", run_kind="agent-repair", run_config=config)
        initial_states.append(modelrepair.CandidateState(
            retained_source, att, ws / f"{tag}.o" if att.compiled else None,
            tuple(item.get("hypotheses", [])), tuple(item.get("kinds", []))))

    panel = None
    if resilient:
        from eval.semantic_lane import DeferredPanel
        panel = DeferredPanel(repo,ws,function,semantic_cases,semantic_steps)
    search = modelrepair.search(
        repo, function, source, ws, model=model, endpoint=endpoint, conn=conn,
        base_attempt=root, base_object_path=root_object,
        parent_attempt_id=root.receipt_id, diagnosis=diagnosis, draws=draws,
        max_depth=depth, beam_width=beam, max_calls=max_calls,
        timeout=timeout, think=think, num_thread=num_thread,
        temperature=temperature, num_predict=num_predict, seed=seed,
        run_id=run_id, cache_dir=cache_dir,
        cache_namespace=f"agentrepair-v1-{function}", provider=provider,
        verbose=verbose, initial_states=tuple(initial_states),
        strategy_brief=strategy_brief, structured_output=structured_output,
        retry_invalid=retry_invalid, include_header_context=include_header_context,
        compile_only=compile_only, type_transaction=type_transaction,
        resilient=resilient,semantic_evaluator=panel)

    best_source_out.parent.mkdir(parents=True, exist_ok=True)
    best_source_out.write_text(search.best_source, encoding="utf-8")
    best_packet = residual.build(
        search.best_attempt, target_asm=target_asm,
        target_object=ws / "target.o",
        candidate_object=search.best_object_path)
    deterministic_counts = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(compiled),0) FROM attempts WHERE run_id=? "
        "AND strategy LIKE 'repair-d%'", (run_id,)).fetchone()
    receipt = {
        "schema_version": 1,
        "kind": "interactive-residual-repair",
        "run_id": run_id,
        "created_at": int(time.time()),
        "config": config,
        "context_reports":context_reports,
        "semantic_panel":panel.report if panel else None,
        "root": {
            "attempt_id": root.receipt_id,
            "source_parent_attempt_id": source_parent_attempt_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "residual": root_packet.to_dict(),
        },
        "result": {
            "exact": search.exact,
            "best_attempt_id": search.best_attempt.receipt_id,
            "verification": search.best_attempt.verification,
            "best_source_sha256": hashlib.sha256(
                search.best_source.encode()).hexdigest(),
            "best_source_path": str(best_source_out),
            "best_residual": best_packet.to_dict(),
            "calls_attempted": search.calls_attempted,
            "generations": search.generations,
            "recorded_tokens": search.tokens,
            "charged_tokens": search.charged_tokens,
            "compiling_children": search.compiling_children,
            "deterministic_candidates": int(deterministic_counts[0]),
            "deterministic_compiling_children": int(deterministic_counts[1]),
            "best_score_improved": search.best_attempt.score > root.score,
            "incomplete_responses": search.incomplete_responses,
            "invalid_proposals": search.invalid_proposals,
            "log": search.log,
            "deterministic_log": deterministic_log,
            "normalization_candidates":search.normalization_candidates,
            "constraint_candidates":sum('type-constraints' in s.kinds for s in initial_states),
            "constraint_compiling_children":sum('type-constraints' in s.kinds and s.attempt.compiled for s in initial_states),
            "semantic_validation":next((state.semantic for state in
                [*search.frontier,search.best_semantic,search.best_byte]
                if state and state.source==search.best_source),None),
            "champions": {label:{'attempt_id':state.attempt.receipt_id,
                'source_sha256':hashlib.sha256(state.source.encode()).hexdigest(),
                'score':state.attempt.score,'semantic':state.semantic}
                for label,state in [('byte',search.best_byte),('semantic',search.best_semantic)] if state},
            "frontier": [{"attempt_id": state.attempt.receipt_id,
                          "source_sha256": hashlib.sha256(state.source.encode()).hexdigest(),
                          "score": state.attempt.score,
                          "compiled": state.attempt.compiled,"semantic":state.semantic,
                          "kinds": list(state.kinds), "hypotheses": list(state.labels)}
                         for state in search.frontier],
        },
    }
    _atomic_json(out, receipt)
    conn.close()
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--function", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path)
    source.add_argument("--attempt-id", type=int)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--best-source-out", type=Path)
    parser.add_argument(
        "--set", dest="set_path", type=Path, default=Path("eval/sets"),
        help="frozen set file or directory; every held-out name is refused")
    parser.add_argument("--model", default="gpt-oss:20b")
    parser.add_argument(
        "--provider", default="solver.modelrepair:OllamaProvider",
        help="no-argument provider factory in module:attribute form")
    parser.add_argument("--endpoint")
    parser.add_argument("--draws", type=int, default=1)
    parser.add_argument("--depth", type=int, default=4)
    parser.add_argument("--beam", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--think", default="low")
    parser.add_argument("--num-thread", type=int, default=12)
    parser.add_argument("--temperature", type=float, default=0.35)
    parser.add_argument("--num-predict", type=int, default=1800)
    parser.add_argument("--seed", type=int, default=20260901)
    parser.add_argument("--deterministic-budget", type=int, default=0)
    parser.add_argument("--deterministic-depth", type=int, default=2)
    parser.add_argument("--structured-output", action="store_true")
    parser.add_argument("--strategy-brief", default="")
    parser.add_argument("--retry-invalid", action="store_true")
    parser.add_argument("--include-header-context", action="store_true")
    parser.add_argument("--type-transaction", action="store_true",
                        help="coordinated slot-only type repair with bounded nonmonotone lookahead")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--resilient",action="store_true",help="recover context/types, normalize C89, and run semantic-first candidate checks")
    parser.add_argument("--semantic-cases",type=int,default=64)
    parser.add_argument("--semantic-steps",type=int,default=10000)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    repo = args.repo.expanduser().resolve()
    db = args.db.expanduser().resolve()
    set_path = args.set_path.expanduser().resolve() if args.set_path else None
    _refuse_frozen_heldout(set_path, args.function)
    conn = sqlite3.connect(db, timeout=120)
    try:
        if args.source:
            candidate = args.source.expanduser().read_text(encoding="utf-8")
            parent_attempt_id = None
        else:
            candidate = _source_for_attempt(conn, args.attempt_id, args.function)
            parent_attempt_id = args.attempt_id
    finally:
        conn.close()

    out = args.out.expanduser().resolve()
    best_out = (args.best_source_out.expanduser().resolve()
                if args.best_source_out else
                out.with_name(f"{out.stem}.best.c"))
    provider = _load_provider(args.provider)
    receipt = run(
        repo=repo, db=db, function=args.function, source=candidate,
        source_parent_attempt_id=parent_attempt_id, out=out,
        best_source_out=best_out, model=args.model,
        endpoint=args.endpoint or llm.host(), draws=args.draws,
        depth=args.depth, beam=args.beam, max_calls=args.max_calls,
        timeout=args.timeout, think=args.think, num_thread=args.num_thread,
        temperature=args.temperature, num_predict=args.num_predict,
        seed=args.seed,
        cache_dir=args.cache_dir.expanduser().resolve()
        if args.cache_dir else None,
        verbose=args.verbose, provider=provider,
        deterministic_budget=args.deterministic_budget, deterministic_depth=args.deterministic_depth,
        structured_output=args.structured_output, strategy_brief=args.strategy_brief,
        retry_invalid=args.retry_invalid, include_header_context=args.include_header_context,
        type_transaction=args.type_transaction, compile_only=args.compile_only,
        resilient=args.resilient,semantic_cases=args.semantic_cases,semantic_steps=args.semantic_steps)
    print(json.dumps(receipt["result"], indent=2))


if __name__ == "__main__":
    main()
