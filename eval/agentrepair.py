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
    # Checkpoints can contain large execution traces. Stream serialization so
    # we do not allocate another full JSON string and encoded byte buffer.
    # Large buffers avoid thousands of small writes across WSL's Windows mount.
    # Compact separators preserve all data without checkpoint indentation.
    with temporary.open("w", encoding="utf-8", buffering=1024 * 1024) as stream:
        json.dump(value, stream, separators=(",", ":"))
        stream.write("\n")
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


def _regalloc_search(repo, conn, ws, function, source, root_packet, budget, *,
                     run_id, config, root_attempt_id):
    """Zero-model register search with durable, parented compiler observations."""
    from solver import regalloc_search
    if not regalloc_search.register_dominant(getattr(root_packet, 'faults', None) or {}):
        return None
    target = ws / 'target_object_dump_normalized.s'
    if not target.is_file():
        return None
    attempt_by_source = {hashlib.sha256(source.encode()).hexdigest(): root_attempt_id}

    def compile_with_parent(candidate, label, parent_source):
        parent_hash = hashlib.sha256((parent_source if parent_source is not None else source).encode()).hexdigest()
        if parent_hash not in attempt_by_source:
            raise RuntimeError(f'regalloc parent was not compiled: {label}')
        tag = f'{function}_regalloc_{time.time_ns()}'
        attempt = workspace.score(ws, repo, tag, candidate, conn=conn, func=function,
                                  strategy='regalloc-search-probe', model='zero-model',
                                  run_id=run_id, parent_attempt_id=attempt_by_source[parent_hash],
                                  relation='regalloc-search', action=label,
                                  run_kind='agent-repair', run_config=config,
                                  extra={'parent_source_sha256': parent_hash})
        if attempt.receipt_id is None:
            raise RuntimeError(f'regalloc attempt was not recorded: {label}')
        attempt_by_source[hashlib.sha256(candidate.encode()).hexdigest()] = attempt.receipt_id
        dump = ws / f'{tag}_object_dump_normalized.s'
        text = dump.read_text() if attempt.compiled and dump.is_file() else None
        obj = ws / f'{tag}.o'
        # kept before cleanup: a reused result is checked against this object by certificate
        obj_bytes = obj.read_bytes() if attempt.compiled and obj.is_file() else None
        for path in ws.glob(tag + '*'):
            if path.is_file():
                path.unlink(missing_ok=True)
        evidence = {'compiled': bool(attempt.compiled), 'score': attempt.score,
                    'source_attribution': attempt.source_attribution,
                    'frontend': attempt.frontend, 'compiler_recipe': attempt.compiler_recipe}
        return regalloc_search.Compiled(bool(attempt.compiled), workspace.repair_complete(attempt),
                                        text, attempt.diff or '', evidence, obj=obj_bytes)

    from kb.attempts import record_model_proposal
    from solver import ido_stages

    def optimizer_key(candidate):
        return ido_stages.optimizer_key(repo, ws, function, candidate)

    def resolved(candidate, label, parent_source, same_as):
        # Never compiled, so no attempts row: the candidate is still logged, with the compile it reused.
        parent_hash = hashlib.sha256((parent_source if parent_source is not None else source).encode()).hexdigest()
        record_model_proposal(conn, run_id=run_id, parent_attempt_id=attempt_by_source.get(parent_hash),
                              prompt='', raw_response=candidate, status='duplicate', model='zero-model',
                              kind='optimizer-key:regalloc', hypothesis=label,
                              edits=[{'same_object_as_attempt': attempt_by_source.get(
                                  hashlib.sha256(same_as.encode()).hexdigest())}])

    def same_object(prior, actual):
        """Object certificate between two candidates' objects (allocated sections and relocation
        expressions); None when either object is missing or the certificate is unverified."""
        from solver import byte_certificate
        if prior.obj is None or actual.obj is None:
            return None
        stem = ws / f'{function}_keycheck_{time.time_ns()}'
        left, right = stem.with_suffix('.a.o'), stem.with_suffix('.b.o')
        try:
            left.write_bytes(prior.obj)
            right.write_bytes(actual.obj)
            receipt = byte_certificate.certify(left, right, source=source)
        finally:
            left.unlink(missing_ok=True)
            right.unlink(missing_ok=True)
        return None if receipt.get('status') == 'unverified' else bool(receipt.get('exact'))

    # Keyed resolution (eval/results/regalloc-keyed-20260927/RESULTS.md): 61% of evaluations resolved, 0 key
    # violations, 73% more candidates per budget, better gradient in 11/40 and worse in 0. Reuse is checked
    # by object certificate on expansion and on a seeded 2% audit (docs/claude-review-followup-20260928.md §2).
    audit_seed = int(hashlib.sha256(f'{run_id}:{function}'.encode()).hexdigest()[:8], 16)
    outcome = regalloc_search.search(function, source, compile_with_parent, target.read_text(),
                                     budget=budget, enable=True,
                                     compile_with_parent=compile_with_parent,
                                     key=optimizer_key, resolved=resolved,
                                     same_object=same_object, audit_rate=0.02, audit_seed=audit_seed,
                                     # scalar coalescing: 2 exact on a one-step sweep of 831 pending functions
                                     # (eval/results/coalescing-sweep-20260928); scoped_field is on by default
                                     # in regalloc_mutations (2/6 transfer, eval/results/scoped-field-20260928)
                                     coalesce=True)
    outcome.best_attempt_id = attempt_by_source[hashlib.sha256(outcome.best_source.encode()).hexdigest()]
    return outcome


def _compile_chain(repo, ws, function, source, recovery_seed, initial_states, compiled_names, conn,
                   run_id, config, context_reports):
    """Chain placeholder/undeclared-identifier fixes from the most advanced non-compiling state."""
    from solver import compile_chain, placeholder_declarations
    pool = [recovery_seed, *(s for s in initial_states if not s.attempt.compiled)]
    base_lines = source.count('\n')
    parent = max(pool, key=lambda s: compile_chain.progress(s.attempt, s.source, base_lines))
    objects = {}

    def score(label, code):
        tag = f'{function}_chain_{time.time_ns()}'
        tag, att = compiled_names.score(tag, code, conn=conn, func=function,
            strategy='agentrepair-compile-chain:' + label, model='zero-model', run_id=run_id,
            parent_attempt_id=parent.attempt.receipt_id, relation='compile-chain', action=label,
            run_kind='agent-repair', run_config=config)
        objects[code] = ws / (tag + '.o') if att.compiled else None
        return att

    try:
        headers = placeholder_declarations.header_names(repo, parent.source)
        chained, log = compile_chain.chain(function, 'chain', parent.source, parent.attempt, score, headers=headers)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        chained, log = [], [{'stage': 'compile-chain', 'status': 'error', 'reason': str(exc)}]
    context_reports.append({'kind': 'compile-chain', 'parent_attempt_id': parent.attempt.receipt_id,
                            'compiled': any(a.compiled for _l, _c, a in chained), 'log': log})
    seen = {s.source for s in initial_states} | {source}
    for label, code, att in chained:
        if code not in seen:
            seen.add(code)
            initial_states.append(modelrepair.CandidateState(code, att, objects.get(code),
                parent.labels + (label,), parent.kinds + ('compile-chain',)))


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
        semantic_cases: int = 64, semantic_steps: int = 10000,
        investigate: bool = False, runtime_captures: tuple[dict, ...] = (),
        recordings: tuple[dict, ...] = (), exhaust_budget: bool = False,
        call_seeds: tuple[int, ...] | None = None, memory_context: dict | None = None,
        regalloc_budget: int = 0, frontend_fixits: bool = False, address_rounds: int = 0,
        structural_rounds: int = 0, stack_rounds: int = 0,
        investigation_policy: dict | None = None, experiment_memory: Path | None = None,
        investigation_identity: dict | None = None, capability_issues: dict | None = None,
        project: Path | None = None, compiler_localization: bool = False) -> dict:
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
        "compiler_localization": compiler_localization,
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
        "regalloc_budget": regalloc_budget,
        "frontend_fixits": frontend_fixits,
        "address_rounds": address_rounds,
        "structural_rounds": structural_rounds,
        "stack_rounds": stack_rounds,
    }
    if memory_context:
        config['binary_data_memory_sha256'] = memory_context['sha256']
    if investigate:
        config['investigation'] = {'enabled': True, 'calls_share_model_budget': True,
                                   'compiler_probes_share_tool_compile_budget': True}
        if investigation_policy:
            config['investigation'].update(policy=investigation_policy,
                experiment_memory=str(experiment_memory) if experiment_memory else None)
    if runtime_captures:
        config['runtime_captures'] = [record['sha256'] for record in runtime_captures]
    if recordings:
        from eval import trace_panel
        config['recordings'] = [trace_panel.identity(record) for record in recordings]
    if call_seeds is not None:
        config['call_seeds'] = list(call_seeds)

    from solver.fresh_compile import Names
    compiled_names = Names(repo, ws)
    root_tag, root = compiled_names.score(
        f"{function}_agentrepair_root_{time.time_ns()}", source, conn=conn, func=function, iteration=0,
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
            tag, att = compiled_names.score(tag,adapted,conn=conn,func=function,
                strategy='agentrepair-context-projection',model='zero-model',run_id=run_id,
                parent_attempt_id=root.receipt_id,relation='build-context-projection',
                action='metadata-only includes/ABI/scoped diagnostics',extra={'context_projection':report},
                run_kind='agent-repair',run_config=config)
            initial_states.append(modelrepair.CandidateState(adapted,att,ws/(tag+'.o') if att.compiled else None,
                ('recovered build context',),('context',)))
        from solver import type_constraints, type_plan, type_transaction as transaction
        seed_state=initial_states[-1] if initial_states else modelrepair.CandidateState(source,root,root_object)
        if (not seed_state.attempt.compiled or
                (seed_state.attempt.frontend or {}).get('passed') is False):
            from solver import compile_recovery
            # Keep the original context as input: header recovery uses a
            # changed include set to trigger its header-aware m2c redraft.
            # Passing only the projected child hides that change entirely.
            recovery_seed=modelrepair.CandidateState(source,root,root_object)
            try:
                recovered,reports=compile_recovery.variants(conn,repo,function,ws,
                    recovery_seed.source,recovery_seed.attempt)
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                recovered,reports=[],[{'stage':'compile-recovery','status':'error','reason':str(exc)}]
            context_reports.append({'parent_attempt_id':recovery_seed.attempt.receipt_id,
                                    'recovery':reports})
            seen={source,*(s.source for s in initial_states)}
            for label,candidate in recovered:
                if candidate in seen:
                    continue
                seen.add(candidate)
                tag=f'{function}_recovery_{time.time_ns()}'
                tag, att=compiled_names.score(tag,candidate,conn=conn,func=function,
                    strategy='agentrepair-compile-recovery:'+label,model='zero-model',run_id=run_id,
                    parent_attempt_id=recovery_seed.attempt.receipt_id,relation='compile-recovery',
                    action=label,extra={'compile_recovery':reports},
                    run_kind='agent-repair',run_config=config)
                initial_states.append(modelrepair.CandidateState(candidate,att,
                    ws/(tag+'.o') if att.compiled else None,
                    recovery_seed.labels+(label,),recovery_seed.kinds+('compile-recovery',)))
            if not any(s.attempt.compiled for s in [seed_state,*initial_states]):
                _compile_chain(repo,ws,function,source,recovery_seed,initial_states,compiled_names,conn,
                               run_id,config,context_reports)
            seed_state=modelrepair._frontier([seed_state,*initial_states],1)[0]
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
                    tag, att=compiled_names.score(tag,candidate,conn=conn,func=function,
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
            run_id=run_id, verbose=verbose, baseline_name=compiled_names.prior(source))
        if candidate != source:
            tag = f"{function}_agentrepair_deterministic_{time.time_ns()}"
            tag, verified = compiled_names.score(tag, candidate, conn=conn, func=function,
                strategy="agentrepair-deterministic-reverify", model=model, run_id=run_id,
                parent_attempt_id=att.receipt_id, relation="agent-repair-root",
                action="reverify deterministic seed", run_kind="agent-repair", run_config=config)
            initial_states.append(modelrepair.CandidateState(
                candidate, verified, ws / f"{tag}.o" if verified.compiled else None,
                ("deterministic compiler search",), ("deterministic",)))
    if regalloc_budget > 0 and root.compiled and not root.exact:
        searched = _regalloc_search(repo, conn, ws, function, source, root_packet, regalloc_budget,
                                    run_id=run_id, config=config, root_attempt_id=root.receipt_id)
        if searched is not None:
            context_reports.append({'kind': 'regalloc-search', **searched.summary(),
                                    'log_tail': searched.log[-20:]})
            if searched.improved and searched.best_source != source:
                tag = f"{function}_agentrepair_regalloc_{time.time_ns()}"
                tag, verified = compiled_names.score(tag, searched.best_source, conn=conn, func=function,
                    strategy="agentrepair-regalloc-search", model="zero-model", run_id=run_id,
                    parent_attempt_id=searched.best_attempt_id, relation="regalloc-search",
                    action=searched.best_label, extra={"regalloc_search": searched.summary()},
                    run_kind="agent-repair", run_config=config)
                initial_states.append(modelrepair.CandidateState(
                    searched.best_source, verified, ws / f"{tag}.o" if verified.compiled else None,
                    ("register search: " + searched.best_label,), ("regalloc",)))
    if frontend_fixits and root.compiled and (root.frontend or {}).get('passed') is False:
        from solver import frontend_fixits as fixits
        command = ((root.frontend or {}).get('recipe') or {}).get('command')
        try:
            fixed, fixit_log = fixits.propose(repo, source, command, ws) if command else (None, [])
            fixit_report = {'kind': 'frontend-fixits', 'status': 'passed' if fixed else
                            'declined' if command else 'no_frontend_recipe', 'rounds': fixit_log}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            fixed, fixit_report = None, {'kind': 'frontend-fixits', 'status': 'error', 'reason': str(exc)}
        context_reports.append(fixit_report)
        if fixed is not None:
            tag = f"{function}_agentrepair_frontend_fixits_{time.time_ns()}"
            tag, verified = compiled_names.score(tag, fixed, conn=conn, func=function,
                strategy="agentrepair-frontend-fixits", model="zero-model", run_id=run_id,
                parent_attempt_id=root.receipt_id, relation="frontend-fixits",
                action="casts at clang-diagnosed expressions", extra={"frontend_fixits": fixit_report},
                run_kind="agent-repair", run_config=config)
            initial_states.append(modelrepair.CandidateState(
                fixed, verified, ws / f"{tag}.o" if verified.compiled else None,
                ("frontend fix-its",), ("frontend-fixits",)))
    if address_rounds > 0 and root.compiled and not root.exact:
        from solver import address_symbols
        import yaml
        segments = address_symbols.segments_from(yaml.safe_load((repo / 'snowboardkids.yaml').read_text()))
        current_source, current = source, root
        for address_round in range(address_rounds):
            chosen = None
            for label, _kind, candidate in address_symbols.variants(current_source, function, current.diff or '',
                                                                    segments=segments):
                tag = f"{function}_agentrepair_address_{time.time_ns()}"
                tag, verified = compiled_names.score(tag, candidate, conn=conn, func=function,
                    strategy="agentrepair-address-symbols", model="zero-model", run_id=run_id,
                    parent_attempt_id=current.receipt_id, relation="address-symbols",
                    action=f"{label} round {address_round}", run_kind="agent-repair", run_config=config)
                if verified.compiled and (chosen is None or
                        modelrepair._quality(verified) > modelrepair._quality(chosen[1])):
                    chosen = (candidate, verified, tag, label)
            if chosen is None or modelrepair._quality(chosen[1]) <= modelrepair._quality(current):
                break
            current_source, current = chosen[0], chosen[1]
            initial_states.append(modelrepair.CandidateState(
                chosen[0], chosen[1], ws / f"{chosen[2]}.o" if chosen[1].compiled else None,
                ("address symbols: " + chosen[3],), ("address-symbols",)))
            context_reports.append({'kind': 'address-symbols', 'round': address_round, 'label': chosen[3],
                                    'score': chosen[1].score, 'exact': chosen[1].exact})
            if chosen[1].exact:
                break
    if structural_rounds > 0 and root.compiled and not root.exact:
        from solver import structural_mutations
        current_source, current = source, root
        for structural_round in range(structural_rounds):
            chosen = None
            for label, _kind, candidate in structural_mutations.variants(current_source, function):
                tag = f"{function}_agentrepair_structural_{time.time_ns()}"
                tag, verified = compiled_names.score(tag, candidate, conn=conn, func=function,
                    strategy="agentrepair-structural-rewrites", model="zero-model", run_id=run_id,
                    parent_attempt_id=current.receipt_id, relation="structural-rewrites",
                    action=f"{label} round {structural_round}", run_kind="agent-repair", run_config=config)
                if verified.compiled and (chosen is None or
                        modelrepair._quality(verified) > modelrepair._quality(chosen[1])):
                    chosen = (candidate, verified, tag, label)
            if chosen is None or modelrepair._quality(chosen[1]) <= modelrepair._quality(current):
                break
            current_source, current = chosen[0], chosen[1]
            initial_states.append(modelrepair.CandidateState(
                chosen[0], chosen[1], ws / f"{chosen[2]}.o" if chosen[1].compiled else None,
                ("structural rewrite: " + chosen[3],), ("structural-rewrites",)))
            context_reports.append({'kind': 'structural-rewrites', 'round': structural_round, 'label': chosen[3],
                                    'score': chosen[1].score, 'exact': chosen[1].exact})
            if chosen[1].exact:
                break
    if stack_rounds > 0 and root.compiled and not root.exact:
        from solver import stack_layout
        current_source, current = source, root
        for stack_round in range(stack_rounds):
            chosen = None
            for label, _kind, candidate in stack_layout.variants(current_source, function, current.diff or ''):
                tag = f"{function}_agentrepair_stack_{time.time_ns()}"
                tag, verified = compiled_names.score(tag, candidate, conn=conn, func=function,
                    strategy="agentrepair-stack-layout", model="zero-model", run_id=run_id,
                    parent_attempt_id=current.receipt_id, relation="stack-layout",
                    action=f"{label} round {stack_round}", run_kind="agent-repair", run_config=config)
                if verified.compiled and (chosen is None or
                        modelrepair._quality(verified) > modelrepair._quality(chosen[1])):
                    chosen = (candidate, verified, tag, label)
                if verified.exact:
                    break
            if chosen is None or modelrepair._quality(chosen[1]) <= modelrepair._quality(current):
                break
            current_source, current = chosen[0], chosen[1]
            initial_states.append(modelrepair.CandidateState(
                chosen[0], chosen[1], ws / f"{chosen[2]}.o" if chosen[1].compiled else None,
                ("stack layout: " + chosen[3],), ("stack-layout",)))
            context_reports.append({'kind': 'stack-layout', 'round': stack_round, 'label': chosen[3],
                                    'score': chosen[1].score, 'exact': chosen[1].exact})
            if chosen[1].exact:
                break
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
        tag, att = compiled_names.score(tag, retained_source, conn=conn, func=function,
            strategy="agentrepair-frontier-reverify", model=model, run_id=run_id,
            parent_attempt_id=retained_id, relation="agent-repair-root",
            action="reverify retained alternative", run_kind="agent-repair", run_config=config)
        initial_states.append(modelrepair.CandidateState(
            retained_source, att, ws / f"{tag}.o" if att.compiled else None,
            tuple(item.get("hypotheses", [])), tuple(item.get("kinds", []))))

    panel = None
    if resilient:
        from eval.semantic_lane import DeferredPanel
        panel = DeferredPanel(repo,ws,function,semantic_cases,semantic_steps,
                              **({'memory_context':memory_context} if memory_context else {}))
        if runtime_captures:
            from eval.captured_panel import Panel as CapturedPanel
            panel = CapturedPanel(panel, repo, ws, function, runtime_captures)
        if recordings:
            # Outermost, so a disagreement with the real game becomes the
            # model's primary counterexample ahead of synthetic cases.
            from eval.trace_panel import Panel as TracePanel
            panel = TracePanel(panel, repo, ws, function, recordings)
            initial_states.extend(_recorded_member_repairs(
                panel, function, modelrepair._frontier(
                    [modelrepair.CandidateState(source, root, root_object), *initial_states], 1)[0],
                compiled_names, conn, ws, run_id, config, context_reports))
    investigation_result = None
    investigation_receipts = []
    investigation_hypotheses = []
    capability_tasks = []
    evaluator = panel
    if investigate and max_calls:
        from solver import investigation, toolagent
        advanced = bool(investigation_policy)
        tools = investigation.Tools(repo, ws, conn, function, out.parent / (out.stem + '-investigation'),
            panel=panel, advanced=advanced, project=project, issues=capability_issues)
        notebook = None
        if advanced:
            from solver.experiment_memory import Notebook
            notebook = Notebook(experiment_memory or out.parent / 'experiment-memory' / (function + '.jsonl'),
                function, {'target_asm': hashlib.sha256(target_asm.encode()).hexdigest(),
                           'compiler': investigation.compiler_identity(repo, root.compiler_recipe),
                           'evidence': investigation_identity or {}})
            evaluator = lambda candidate: tools.evaluate(candidate, panel)
        start = modelrepair._frontier([modelrepair.CandidateState(source, root, root_object), *initial_states], 1)[0]
        behavior = panel(start) if panel else None
        investigation_question = investigation.question({'status': 'pending',
            'source_sha256': hashlib.sha256(start.source.encode()).hexdigest(),
            'residual': {'compiled': start.attempt.compiled, 'frontend': start.attempt.frontend},
            'semantic_validation': behavior})
        if behavior:
            behavior = {k: behavior.get(k) for k in ('status', 'source_sha256', 'panel_sha256', 'counts', 'reason')}
            original_behavior = start.semantic or (panel(start) if panel else {}) or {}
            behavior['feedback'] = original_behavior.get('feedback', [])[:1]
            behavior['operation_gradient'] = str(original_behavior.get('operation_gradient', ''))[:4000]
        investigation_result = toolagent.search(repo, function, start.source, ws,
            model=model, endpoint=endpoint, conn=conn, base_attempt=start.attempt,
            base_object_path=start.object_path, parent_attempt_id=start.attempt.receipt_id,
            diagnosis=diagnosis + '\n' + strategy_brief + '\n' + json.dumps(investigation_question)
                + '\nBinary evidence is available for the function symbol ' + function
                + '. If header lookup fails, inspect target evidence or the instruction diff.'
                + '\nBehavioral evidence: ' + json.dumps(behavior),
            provider=provider, max_calls=max_calls,
            max_compiles=investigation_policy['compiles'] if advanced else max_calls,
            timeout=timeout, think=think, num_thread=num_thread, temperature=temperature,
            num_predict=num_predict, seed=seed, run_id=run_id + '-investigation',
            call_seeds=call_seeds,
            investigation_tools=tools.handlers(), semantic_evaluator=evaluator,
            allow_reconstruction=advanced, notebook=notebook,
            max_seconds=investigation_policy['seconds'] if advanced else None)
        for child in investigation_result.candidates or [investigation_result.best]:
            initial_states.append(modelrepair.CandidateState(child.source, child.attempt, child.object_path,
                ('investigation candidate',), ('investigation',), child.semantic))
        investigation_receipts = tools.receipts
        investigation_hypotheses = tools.hypotheses
        capability_tasks = tools.capability_tasks
    search = modelrepair.search(
        repo, function, source, ws, model=model, endpoint=endpoint, conn=conn,
        base_attempt=root, base_object_path=root_object,
        parent_attempt_id=root.receipt_id, diagnosis=diagnosis, draws=draws,
        max_depth=depth, beam_width=beam, max_calls=0 if investigate else max_calls,
        timeout=timeout, think=think, num_thread=num_thread,
        temperature=temperature, num_predict=num_predict, seed=seed,
        call_seeds=call_seeds,
        run_id=run_id, cache_dir=cache_dir,
        cache_namespace=f"agentrepair-v1-{function}", provider=provider,
        verbose=verbose, initial_states=tuple(initial_states),
        strategy_brief=strategy_brief, structured_output=structured_output,
        retry_invalid=retry_invalid, include_header_context=include_header_context,
        compile_only=compile_only, type_transaction=type_transaction,
        resilient=resilient,semantic_evaluator=evaluator, exhaust_budget=exhaust_budget,
        compiler_localization=compiler_localization)

    if investigation_result:
        search.calls_attempted += investigation_result.calls_attempted
        search.generations += investigation_result.generations
        search.tokens += investigation_result.tokens
        search.charged_tokens += investigation_result.charged_tokens
        search.invalid_proposals += investigation_result.invalid_actions
        search.compiling_children += sum(c.parent_id is not None and c.attempt.compiled
                                         for c in investigation_result.candidates)
        search.log.extend(investigation_result.events)

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
        "investigation": {"enabled": investigate, "observations": investigation_receipts,
                          "hypotheses": investigation_hypotheses,
                          "capability_tasks": capability_tasks,
                          "compile_attempts": investigation_result.compiles if investigation_result else 0,
                          "events": investigation_result.events if investigation_result else []},
        "semantic_panel":panel.report if panel else None,
        "root": {
            "attempt_id": root.receipt_id,
            "source_parent_attempt_id": source_parent_attempt_id,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "residual": root_packet.to_dict(),
        },
        "result": {
            "investigation": {"observations": investigation_receipts, "hypotheses": investigation_hypotheses,
                              "capability_tasks": capability_tasks},
            "exact": search.exact,
            "best_attempt_id": search.best_attempt.receipt_id,
            "verification": search.best_attempt.verification,
            "best_source_sha256": hashlib.sha256(
                search.best_source.encode()).hexdigest(),
            "best_source_path": str(best_source_out),
            "best_residual": best_packet.to_dict(),
            "calls_attempted": search.calls_attempted,
            "transport_events": search.transport_events,
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
    parser.add_argument("--recordings", type=Path,
                        help="directory of recorded game calls (call-*.json); requires --resilient")
    parser.add_argument("--exhaust-budget", action="store_true",
                        help="retry parents when a depth yields no evaluated child, until --max-calls")
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
        resilient=args.resilient,semantic_cases=args.semantic_cases,semantic_steps=args.semantic_steps,
        recordings=_load_recordings(args.recordings, args.function, args.resilient),
        exhaust_budget=args.exhaust_budget)
    print(json.dumps(receipt["result"], indent=2))


def _recorded_member_repairs(panel, function, start, compiled_names, conn, ws, run_id, config, reports,
                             rounds=3):
    """Zero-model member renames from recorded offset constraints, verified by compile and replay."""
    from solver import recorded_layout
    states, current = [], start
    for index in range(rounds):
        if not current.attempt.compiled or current.object_path is None:
            break
        behaviour = panel(current) or {}
        failed = [r for r in behaviour.get("recorded_call_results", []) if r.get("status") == "failed"]
        constraints = [c for r in failed for c in r.get("offset_constraints", [])]
        if not constraints:
            if failed:
                reports.append({"kind": "recorded-member-repair", "round": index, "status": "no_offset_constraints",
                                "failed_recordings": len(failed)})
            break
        fields, note = panel.fields(current)
        candidate, report = recorded_layout.propose(current.source, function, constraints, fields)
        report.update(round=index, parent_attempt_id=current.attempt.receipt_id,
                      **({"field_names": note} if note else {}))
        reports.append(report)
        if candidate is None:
            break
        tag = f"{function}_recorded_layout_{time.time_ns()}"
        tag, attempt = compiled_names.score(
            tag, candidate, conn=conn, func=function, iteration=0,
            strategy="agentrepair-recorded-member-repair", model="zero-model", run_id=run_id,
            parent_attempt_id=current.attempt.receipt_id, relation="recorded-member-repair",
            action="rename members to the offsets the recorded game used",
            extra={"recorded_member_repair": report}, run_kind="agent-repair", run_config=config)
        report["result"] = {"compiled": attempt.compiled, "score": attempt.score, "exact": attempt.exact}
        state = modelrepair.CandidateState(candidate, attempt, ws / f"{tag}.o" if attempt.compiled else None,
                                           current.labels + ("recorded member repair",),
                                           current.kinds + ("recorded-layout",))
        states.append(state)
        if not attempt.compiled or attempt.exact:
            break
        current = state
    return states


def _load_recordings(directory, function, resilient):
    if directory is None:
        return ()
    if not resilient:
        raise SystemExit("--recordings needs --resilient: recordings are checked by the semantic panel")
    records = [json.loads(path.read_text()) for path in sorted(Path(directory).glob("call-*.json"))]
    records = tuple(r for r in records if r.get("function") == function)
    if not records:
        raise SystemExit(f"no recordings for {function} in {directory}")
    return records


if __name__ == "__main__":
    main()
