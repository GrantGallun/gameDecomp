"""Opt-in, isolated, compiling path reconstruction. Never imports partial work.

init copies full history and a private function workspace; advance fills one
explicit hole per proposal. Original drafts and every child remain durable.
No canonical campaign candidate, game source or evidence table is modified.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.error

from eval import agentrepair, campaign_state, campaign_workers, completion_campaign, frozen_wavefront, semantic_lane
from kb import attempts
from solver import branch_context, cfg, llm, modelrepair, partial_reconstruction as partial, refine, workspace
from solver import mips_differential as differential

PROJECT = Path(__file__).resolve().parents[1]
SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['manifest_sha256', 'hole_id', 'replacement', 'implemented_blocks', 'child_holes', 'hypothesis'],
    'properties': {
        'manifest_sha256': {'type': 'string'}, 'hole_id': {'type': 'integer', 'minimum': 0},
        'hypothesis': {'type': 'string'},
        'replacement': {'type': 'string'},
        'implemented_blocks': {'type': 'array', 'items': {'type': 'integer'}},
        'child_holes': {'type': 'array', 'maxItems': 16, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['id', 'blocks'],
            'properties': {'id': {'type': 'integer', 'minimum': 0},
                           'blocks': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer'}}}}}}}

# Keep string length bounds in local validation. Ollama's grammar compiler
# expands bounded strings into repetitions and rejects even a 2000-char cap.
GUARD_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['prefix', 'condition', 'hypothesis'], 'properties': {
        'prefix': {'type': 'string'},
        'condition': {'type': 'string'},
        'hypothesis': {'type': 'string'}}}


def entry_guard_plan(manifest, hole_id):
    """Own the ledger for a resolved initial diamond; never infer C semantics.

    This deliberately declines loops back to entry, unknown transfers, multiple
    joins, and already-partitioned functions. Branch bodies and shared tail stay
    unfinished. Only the model-proposed entry block is declared implemented.
    """
    graph = cfg.build(manifest['target_assembly'])
    if (manifest['revision'] != 0 or len(manifest['holes']) != 1
            or manifest['holes'][0]['id'] != hole_id or len(graph.blocks) <= 8
            or graph.reachable() != set(graph.blocks)):
        return None
    entry = graph.blocks[graph.entry]
    term = entry.terminator
    if (not term or not cfg.is_conditional_branch(term.opcode) or term.branch_likely
            or len(entry.successors) != 2 or len(entry.instructions) > 96
            or any(b.unknown_successor for b in graph.blocks.values())):
        return None
    taken = graph.instruction_to_block[graph.label_to_instruction[term.target]]
    fallthrough = next(s for s in entry.successors if s != taken)
    def reachable(start):
        seen, todo = set(), [start]
        while todo:
            node = todo.pop()
            if node in seen:
                continue
            seen.add(node)
            todo.extend(graph.blocks[node].successors - seen)
        return seen
    yes, no = reachable(taken), reachable(fallthrough)
    if graph.entry in yes | no:
        return None
    common = yes & no
    joins = {b for b in common if graph.blocks[b].predecessors - common}
    if len(joins) > 1:
        return None
    groups = [('taken', yes-no), ('fallthrough', no-yes), ('shared_tail', common)]
    children, roles = [], {}
    for role, blocks in groups:
        if blocks:
            ident = manifest['max_hole_id'] + 1 + len(children)
            children.append({'id': ident, 'blocks': sorted(blocks)})
            roles[role] = ident
    if set().union(*(set(c['blocks']) for c in children), {graph.entry}) != set(graph.blocks):
        return None
    return {'manifest_sha256': manifest['manifest_sha256'], 'hole_id': hole_id,
        'implemented_blocks': [graph.entry], 'child_holes': children, 'roles': roles,
        'entry_instructions': [i.text for i in entry.instructions],
        'branch_instruction': term.text, 'taken_successor': taken,
        'fallthrough_successor': fallthrough}


def guard_proposal(plan, response):
    """Convert a small model hypothesis to the ordinary strict proposal format."""
    if not isinstance(response, dict) or set(response) != {'prefix', 'condition', 'hypothesis'}:
        raise ValueError('entry guard response needs exactly prefix, condition, hypothesis')
    for key, limit in [('prefix', 2000), ('condition', 512), ('hypothesis', 600)]:
        if not isinstance(response[key], str) or len(response[key]) > limit:
            raise ValueError('invalid entry guard ' + key)
    condition = response['condition'].strip()
    if (not condition or re.search(r'[;{}#\\]|\b(?:return|goto|if|else|for|while|switch)\b', condition)
            or partial.MARKER in condition or partial.MARKER in response['prefix']):
        raise ValueError('entry guard must be an expression without control flow or markers')
    if re.search(r'\b(?:return|goto|if|else|for|while|do|switch|break|continue)\b', response['prefix']):
        raise ValueError('entry guard prefix must be straight-line declarations and statements')
    roles = plan['roles']
    statement = lambda role: partial.marker(roles[role]) if role in roles else ''
    replacement = ('{\n' + response['prefix'] + '\nif (' + condition + ') {\n' + statement('taken')
        + '\n} else {\n' + statement('fallthrough') + '\n}\n' + statement('shared_tail') + '\n}')
    return {k: plan[k] for k in ('manifest_sha256', 'hole_id', 'implemented_blocks', 'child_holes')} | {
        'replacement': replacement, 'hypothesis': response['hypothesis']}


def guard_prompt(parent, plan, recent=()):
    manifest = parent['manifest']
    packet = {'entry_instructions': plan['entry_instructions'], 'branch_instruction': plan['branch_instruction'],
        'current_c': manifest['source'], 'original_unverified_draft_prefix': manifest['original_source'][:1600],
        'source_sha256': manifest['source_sha256'], 'manifest_sha256': manifest['manifest_sha256'],
        'recent_rejections': [{'error': c.get('error'), 'compiler_error': c.get('compiler_error', '')[:1500]}
                              for c in list(recent)[-2:]]}
    return ('Translate ONLY the entry basic block into a C89 prefix and the condition under which the displayed '
        'MIPS branch is TAKEN. Return JSON with prefix, condition, hypothesis. condition is a C expression; '
        'prefix is usually empty or local declarations/assignments. Include actual external effects before the branch '
        'and its ordinary delay slot; compiler manages stack-frame setup and register saves. '
        'Use existing C parameter/global names and headers. No function definition, helpers, Markdown, or unfinished '
        'marker calls. Do not implement either branch body or the rest of the function. The controller generates '
        'if (condition) { unfinished taken path; } else { unfinished fallthrough; } followed by an unfinished shared tail '
        'and owns all block accounting. Keep your answer short; do not decompile beyond these instructions.\n'
        + json.dumps(packet))


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def copy_history(source, destination):
    """Consistent full SQLite backup in short steps, yielding to live writers."""
    temporary = destination.with_suffix('.copying.sqlite')
    if destination.exists() or temporary.exists():
        raise ValueError('private history already exists')
    with closing(sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=120)) as src:
        with closing(sqlite3.connect(temporary)) as dst:
            src.backup(dst, pages=128, sleep=0.01)
            cutoffs = campaign_workers.maxima(dst)
    temporary.replace(destination)
    return cutoffs


def attempt_summary(att):
    return {key: getattr(att, key) for key in
        ('compiled', 'score', 'exact', 'receipt_id', 'compiler_stderr', 'frontend', 'verification')}


def classify(manifest, cases, rows):
    """Preserve raw outcomes; holes suppress all behavioral credit for that case."""
    partial.validate(manifest)
    observations = []
    for case, row in zip(cases, rows):
        markers = [call.raw_arguments[0] if call.raw_arguments else None
                   for call in row.candidate.calls if call.callee == partial.MARKER]
        result = partial.classify_row(manifest, {'status': row.status, 'reasons': list(row.reasons),
            'first_divergence': row.first_divergence}, [e.instruction for e in row.target.trace], markers,
            trace_complete=row.target.status in differential.COMPLETED_STATUSES
                and row.candidate.status in differential.COMPLETED_STATUSES)
        result.update(case=asdict(case), case_sha256=sha(json.dumps(asdict(case), sort_keys=True)))
        observations.append(result)
    passed = [r['case_sha256'] for r in observations if r['eligible_for_behavior_credit'] and r['raw']['status'] == 'passed']
    return {'status': 'partial_observations', 'authoritative': False,
        'counts': dict(Counter('unfinished' if not r['eligible_for_behavior_credit'] else r['raw']['status'] for r in observations)),
        'raw_counts': dict(Counter(r.status for r in rows)), 'passed_cases': passed,
        'cases': observations, 'pending_blocks': sum(len(h['blocks']) for h in manifest['holes']),
        'declared_implemented_blocks': len(manifest['implemented_blocks']),
        'scope': 'Block ownership is a reconstruction hypothesis; only complete executions without holes earn observed case credit.'}


def admissible(parent, child):
    """Retain compilation and every previously passing whole execution."""
    if not child['attempt']['compiled'] or (child['attempt']['frontend'] or {}).get('passed') is not True:
        return False, 'compiler/frontend rejected'
    previous = parent.get('observations') or {}
    current = child.get('observations') or {}
    if not set(previous.get('passed_cases', [])) <= set(current.get('passed_cases', [])):
        return False, 'previously passing path regressed or became unfinished'
    if current.get('counts', {}).get('failed', 0):
        return False, 'implemented-path behavior disagrees'
    return True, 'compiling partial retained; declared coverage is not correctness'


def current_status(result):
    if result['complete']:
        return 'object_exact_observed'
    if not result['attempt']['compiled'] or (result['attempt']['frontend'] or {}).get('passed') is not True:
        return 'skeleton_compile_blocked'
    if result.get('semantic_unavailable') or not (result.get('observations') or {}).get('cases'):
        return 'compiling_partial_unvalidated' if result['manifest']['holes'] else 'full_reconstruction_unvalidated'
    return 'compiling_partial' if result['manifest']['holes'] else 'ready_for_full_repair'


def export_full_candidate(state, result):
    state.pop('full_candidate', None)
    if result['manifest']['holes']:
        return
    path = Path(state['output']) / f'full-candidate-{state["current"]:04d}.c'
    path.write_text(result['manifest']['source'])
    state['full_candidate'] = {'source': str(path), 'source_sha256': result['manifest']['source_sha256'],
        'attempt_id': result['attempt']['receipt_id'], 'manifest_sha256': result['manifest']['manifest_sha256'],
        'next': 'Use eval.agentrepair --resilient with this private repo/db and --attempt-id; '
                'normal full-function verification remains required. No automatic campaign import.'}


def make_panel(state, source):
    repo, ws = Path(state['repo']), Path(state['workspace'])
    return semantic_lane.Panel(repo, ws, state['function'], state['semantic_cases'], state['semantic_steps'],
                               state['exploration_cases'], header_source=source)


def evaluate(state, manifest, conn, parent_id, panel=None):
    source = partial.source_for_compile(manifest)
    tag = f"{state['function']}_partial_{time.time_ns()}"
    ws, repo = Path(state['workspace']), Path(state['repo'])
    att = workspace.score(ws, repo, tag, source, conn=conn, func=state['function'],
        run_id=state['run_id'], run_kind='partial-reconstruction', run_config=state['config'],
        parent_attempt_id=parent_id, relation='partial-reconstruction', strategy='fill-explicit-region',
        extra={'manifest_sha256': manifest['manifest_sha256'], 'unfinished_holes': manifest['holes'],
               'authority': 'private reconstruction hypothesis; never campaign promotion'})
    result = {'manifest': manifest, 'attempt': attempt_summary(att), 'object': str(ws / (tag + '.o')),
              'observations': None, 'complete': False}
    if not att.compiled or (att.frontend or {}).get('passed') is not True:
        return result
    try:
        panel = panel or make_panel(state, source)
    except (OSError, ValueError) as exc:
        result['semantic_unavailable'] = str(exc)
        return result
    if panel.target != manifest['target_assembly']:
        raise ValueError('panel/manifest target instruction identity differs')
    if state.get('panel_sha256') not in (None, panel.identity):
        raise ValueError('fixed reconstruction panel changed')
    state['panel_sha256'] = panel.identity
    obj = Path(result['object'])
    assembly = workspace.semantic_assembly(obj.with_name(obj.stem + '_object_dump_normalized.s').read_text(), obj)
    rows = differential.run_suite(panel.target, assembly, panel.cases, target_name=state['function'],
        candidate_name=state['function'] + '-partial', call_arities={**panel.arities, partial.MARKER: 1},
        return_registers=panel.returns, max_steps=panel.max_steps, callee_environment=panel.callee_environment)
    observations = classify(manifest, panel.cases, rows)
    observations.update(panel_sha256=panel.identity, source_sha256=manifest['source_sha256'], debt=panel.debt,
                        target_coverage=panel.report['target_exploration'])
    observations['examples'] = [{'input': asdict(case), 'raw_status': row.status,
        'unfinished': not item['eligible_for_behavior_credit'], 'path_context': branch_context.packet(row, max_decisions=3),
        'causal_feedback': differential.causal_feedback(row, max_steps=6)}
        for case, row, item in list(zip(panel.cases, rows, observations['cases']))[:3]]
    result['observations'] = observations
    if not manifest['holes']:
        # No unfinished accounting or trace intervention is used by this gate.
        full = panel(modelrepair.CandidateState(source, att, obj))
        result['full_semantic'] = full
        result['complete'] = bool(workspace.repair_complete(att) and full and
            full.get('status') in {'observed_pass', 'observed_pass_with_execution_debt'})
    return result


def persist_version(state, result):
    path = Path(state['output']) / f'version-{len(state["versions"]):04d}.json'
    campaign_state.atomic(path, result)
    state['versions'].append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'manifest_sha256': result['manifest']['manifest_sha256'], 'attempt_id': result['attempt']['receipt_id']})
    return len(state['versions']) - 1


def version(state, index=None):
    ref = state['versions'][state['current'] if index is None else index]
    raw = Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('reconstruction version changed')
    result = json.loads(raw)
    partial.validate(result['manifest'])
    return result


def initialize(*, repo, db, function, source, parent_attempt_id, output, work_root,
               semantic_cases=64, semantic_steps=10000, exploration_cases=256):
    if not 1 <= semantic_cases <= 256 or not 1 <= semantic_steps <= 1000000 or not 1 <= exploration_cases <= 5000:
        raise ValueError('invalid bounded semantic budgets')
    repo, db, output, work_root = map(lambda p: Path(p).resolve(), (repo, db, output, work_root))
    agentrepair._refuse_frozen_heldout(PROJECT / 'eval/sets', function)
    if output.exists() or work_root.exists():
        raise ValueError('new isolated output and work directories required')
    if work_root.is_relative_to(repo):
        raise ValueError('private work directory must be outside the original game repository')
    if not (repo / 'nonmatchings' / function / 'target.s').is_file():
        raise ValueError('bootstrap the target before reconstruction; canonical workspace is read-only here')
    with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
        row = conn.execute('SELECT a.source_code,f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE a.id=?',
                           (parent_attempt_id,)).fetchone()
    if row != (source, function):
        raise ValueError('source must match its recorded function/parent attempt')
    output.mkdir(parents=True)
    work_root.mkdir(parents=True)
    private_db = work_root / 'history.sqlite'
    cutoffs = copy_history(db, private_db)
    private_repo = campaign_workers.isolate(repo, work_root / 'repo', function)
    ws = private_repo / 'nonmatchings' / function
    state = {'kind': 'partial-reconstruction-v1', 'function': function, 'output': str(output),
        'repo': str(private_repo), 'workspace': str(ws), 'db': str(private_db),
        'run_id': f'reconstruct-{time.time_ns()}-{function}', 'current': None, 'versions': [], 'calls': [],
        'semantic_cases': semantic_cases, 'semantic_steps': semantic_steps, 'exploration_cases': exploration_cases,
        'upstream': {'db': str(db), 'source_parent_attempt_id': parent_attempt_id, 'cutoffs': cutoffs},
        'config': {'kind': 'private-partial-reconstruction', 'function': function,
                   'source_sha256': sha(source), 'no_automatic_import': True},
        'status': 'initializing', 'complete': False}
    state['pins'] = frozen_wavefront.file_hashes(frozen_wavefront.code_paths(PROJECT))
    with sqlite3.connect(private_db) as conn:
        refine.ensure_schema(conn)
        original = workspace.score(ws, private_repo, function + '_original_' + str(time.time_ns()), source,
            conn=conn, func=function, run_id=state['run_id'], run_kind='partial-reconstruction', run_config=state['config'],
            parent_attempt_id=parent_attempt_id, relation='original-draft-reverify', strategy='partial-original-retained')
        state['original_attempt'] = attempt_summary(original)
        (output / 'original.c').write_text(source)
        campaign_state.atomic(output / 'state.json', state)
        try:
            if not (ws / 'target_object_dump_normalized.s').is_file():
                # A failed original never reached objdump. Bootstrap compiler
                # artifacts with the skeleton first; this temporary raw-asm
                # ledger earns no differential credit or completion.
                seed = partial.create(source, function, (ws / 'target.s').read_text())
                att = workspace.score(ws, private_repo, function + '_skeleton_bootstrap_' + str(time.time_ns()),
                    partial.source_for_compile(seed), conn=conn, func=function, run_id=state['run_id'],
                    run_kind='partial-reconstruction', run_config=state['config'],
                    parent_attempt_id=original.receipt_id, relation='skeleton-artifact-bootstrap',
                    strategy='partial-skeleton-bootstrap', extra={'authority': 'compiler artifacts only; no path credit'})
                state['bootstrap_attempt'] = attempt_summary(att)
                campaign_state.atomic(output / 'state.json', state)
                if not att.compiled:
                    raise ValueError('skeleton still fails compilation: ' + att.compiler_stderr)
            target = workspace.semantic_assembly((ws / 'target_object_dump_normalized.s').read_text(), ws / 'target.o')
            manifest = partial.create(source, function, target)
            # Panel may add measured memory-extent annotations. Bind the exact
            # target text it executes, not a nearly-identical earlier dump.
            panel = None
            try:
                panel = make_panel(state, manifest['source'])
                manifest = partial.create(source, function, panel.target)
            except (OSError, ValueError) as exc:
                state['initial_panel_unavailable'] = str(exc)
            result = evaluate(state, manifest, conn, original.receipt_id, panel)
            state['current'] = persist_version(state, result)
            state['status'] = current_status(result)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            state.update(status='initialization_declined', error=str(exc))
    state['pins'].update(frozen_wavefront.file_hashes([ws / 'target.o', ws / 'target.s',
        ws / 'target_object_dump_normalized.s', ws / '.compiler-target.json']))
    campaign_state.atomic(output / 'state.json', state)
    return state


def prompt_for(parent, hole_id, recent=()):
    manifest = partial.validate(parent['manifest'])
    hole = next(h for h in manifest['holes'] if h['id'] == hole_id)
    graph = cfg.build(manifest['target_assembly'])
    shown, budget = [], 128
    for ident in hole['blocks']:
        block = graph.blocks[ident]
        if len(block.instructions) > budget:
            continue
        shown.append({'id': ident, 'successors': sorted(block.successors), 'unknown_successor': block.unknown_successor,
                      'instructions': [i.text for i in block.instructions]})
        budget -= len(block.instructions)
        if len(shown) >= 8:
            break
    if not shown:
        raise ValueError('selected region has no whole block within the instruction packet budget')
    packet = {'manifest_sha256': manifest['manifest_sha256'], 'hole': hole, 'shown_target_blocks': shown,
        'omitted_blocks': [b for b in hole['blocks'] if b not in {r['id'] for r in shown}],
        'current_c': manifest['source'], 'existing_holes': manifest['holes'],
        'replacement_location': partial.marker(hole_id),
        'replacement_syntax_example': '{ int value; value = 0; /* statements go here, no function header */ }',
        'next_available_child_id': manifest['max_hole_id'] + 1,
        'original_draft_context': {'text': manifest['original_source'][:2000],
                                   'omitted_characters': max(0, len(manifest['original_source'])-2000),
                                   'role': 'existing unverified failed draft, not target source or type authority'},
        'source_sha256': manifest['source_sha256'],
        'observations': {k: (parent.get('observations') or {}).get(k) for k in ('counts', 'examples', 'debt', 'panel_sha256')},
        'recent_rejections': list(recent)[-2:]}
    prompt = ('Reconstruct ONE incomplete region of a C89 function from target instructions. '
        'replacement must contain ONLY C statements replacing the selected __gd_unfinished(id); statement INSIDE '
        'the existing function. NEVER include the function signature, a nested function, prose or Markdown fences. '
        'Preserve the public signature and surrounding source. '
        'Recover a guard and ONE feasible branch first; leave other paths as fresh literal __gd_unfinished(id); calls. '
        'Use a compound block for local declarations. No preprocessor, assembly, goto, helper definitions or reference source. '
        'Return JSON matching the schema. Partition EVERY selected target block exactly once between implemented_blocks '
        'and child_holes[].blocks; each child hole needs exactly one marker call in replacement, with a fresh integer id. '
        'The marker IDs appearing in replacement must equal the child_holes IDs exactly: two marker calls require '
        'two child_holes entries with disjoint block lists. Do not emit undeclared child IDs. '
        'Only shown blocks may be declared implemented; keep omitted blocks in child holes. A block assignment is a '
        'hypothesis, never evidence of correctness. Calls, loads, writes and branch operands must follow the target. '
        'Do not force byte similarity to increase; compiler allocation changes globally as code grows. '
        'Preserve previously passing executions. Dummy paths and their shared tails earn no behavioral credit.\n' +
        json.dumps(packet))
    return prompt, {r['id'] for r in shown}


def advance(state_path, *, max_calls=0, proposal=None, parent_version=None, model='gpt-oss:20b',
            endpoint='http://127.0.0.1:11435', model_lock=None, timeout=240, num_predict=6000,
            seed=1, think='low', provider=None):
    if not 0 <= max_calls <= 16 or (proposal is not None and max_calls):
        raise ValueError('use one saved proposal or a bounded model call count')
    state_path = Path(state_path)
    with completion_campaign.campaign_lock(state_path.with_suffix('.lock')):
        state = json.loads(state_path.read_bytes())
        if state.get('kind') != 'partial-reconstruction-v1' or state['current'] is None:
            raise ValueError('no initialized reconstruction to advance')
        frozen_wavefront.verify_files(state['pins'])
        if parent_version is not None:
            if type(parent_version) is not int or not 0 <= parent_version < len(state['versions']):
                raise ValueError('invalid parent version')
            parent = version(state, parent_version)  # explicit branch, old versions remain intact
            state['current'] = parent_version
            state['complete'] = parent['complete']
            state['status'] = current_status(parent)
            export_full_candidate(state, parent)
        if max_calls and provider is None:
            identity = frozen_wavefront.model_digest(endpoint, model)
            if state.get('model_digest') not in (None, identity):
                raise ValueError('model changed')
            state['model_digest'] = identity
        for iteration in range(1 if proposal is not None else max_calls):
            parent = version(state)
            if not parent['manifest']['holes']:
                state['status'] = current_status(parent)
                break
            hole_id = proposal['hole_id'] if proposal else min(parent['manifest']['holes'], key=lambda h: (len(h['blocks']), h['id']))['id']
            plan = entry_guard_plan(parent['manifest'], hole_id) if proposal is None else None
            if plan:
                prompt, shown = guard_prompt(parent, plan, state['calls']), set(plan['implemented_blocks'])
                schema = GUARD_SCHEMA
            else:
                prompt, shown = prompt_for(parent, hole_id, state['calls'])
                schema = SCHEMA
            raw, metadata, error, bad_request = '', {}, None, False
            parsed = None
            settings = {'model': model, 'endpoint': endpoint, 'timeout': timeout, 'num_predict': num_predict,
                        'seed': seed + len(state['calls']), 'think': think, 'num_ctx': 32768, 'temperature': 0.2}
            started = time.monotonic()
            try:
                if proposal is not None:
                    raw = json.dumps(proposal)
                    metadata = {'provider': 'saved-proposal', 'new_model_call': False}
                elif provider is not None:
                    raw, metadata = provider(prompt, settings, schema)
                else:
                    from eval.fast_runtime import model_lease
                    lock = Path(model_lock) if model_lock else Path(state['output']) / 'model.lock'
                    with model_lease(lock):
                        raw, metadata = llm.generate(endpoint, model, prompt, timeout=timeout, num_thread=12,
                            num_predict=num_predict, think=think, temperature=0.2, seed=settings['seed'],
                            num_ctx=32768, response_schema=schema, transport_attempts=1)
                if len(raw) > 128000:
                    raise ValueError('oversized proposal response')
                parsed = json.loads(raw)
                if plan:
                    parsed = guard_proposal(plan, parsed)
                if not isinstance(parsed, dict) or parsed.get('hole_id') != hole_id:
                    raise ValueError('proposal changed the selected hole')
                if not set(parsed['implemented_blocks']) <= shown:
                    raise ValueError('proposal claims unshown blocks')
                child = partial.apply(parent['manifest'], parsed)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                error = f'{type(exc).__name__}: {exc}'
                if isinstance(exc, urllib.error.HTTPError):
                    error += ': ' + exc.read(4000).decode('utf-8', errors='replace')
                    bad_request = 400 <= exc.code < 500
            call = {'status': 'rejected' if error else 'proposed', 'error': error,
                    'proposal_mode': 'entry-guard' if plan else 'region',
                    'parent_version': state['current'], 'wall_seconds': time.monotonic()-started}
            if plan:
                call['deterministic_plan'] = plan
                if parsed and not error:
                    call['normalized_proposal'] = parsed
            if error and isinstance(parsed, dict):
                call['rejected_replacement'] = str(parsed.get('replacement', ''))[:4000]
                call['declared_child_holes'] = parsed.get('child_holes')
            with sqlite3.connect(state['db']) as conn:
                pid = attempts.record_model_proposal(conn, run_id=state['run_id'],
                    parent_attempt_id=parent['attempt']['receipt_id'], prompt=prompt, raw_response=raw,
                    status=call['status'], model='saved-proposal' if proposal is not None else model,
                    kind='partial-region', sampling={**settings, 'metadata': metadata},
                    wall_ms=int(call['wall_seconds']*1000), token_cost=metadata.get('eval_count', 0))
                call['proposal_id'] = pid
                # Save the raw response before any fallible compilation or evaluation.
                state['calls'].append(call)
                campaign_state.atomic(state_path, state)
                if not error:
                    try:
                        result = evaluate(state, child, conn, parent['attempt']['receipt_id'])
                        attempts.link_model_proposal(conn, pid, result['attempt']['receipt_id'])
                        index = persist_version(state, result)
                        admitted, reason = admissible(parent, result)
                        call.update(child_version=index, status='accepted' if admitted else 'rejected', reason=reason)
                        if admitted:
                            state['current'] = index
                            state['complete'] = result['complete']
                            state['status'] = current_status(result)
                            export_full_candidate(state, result)
                        else:
                            call.update(compiler_error=result['attempt']['compiler_stderr'],
                                frontend=result['attempt']['frontend'], rejected_replacement=parsed['replacement'][:4000],
                                observed_counts=(result.get('observations') or {}).get('counts'))
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                        call.update(status='evaluation_error', error=f'{type(exc).__name__}: {exc}')
            frozen_wavefront.verify_files(state['pins'])
            campaign_state.atomic(state_path, state)
            if bad_request:
                break  # repeating an invalid request cannot improve its C hypothesis
        campaign_state.atomic(state_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init')
    for name in ('repo', 'db', 'source', 'output', 'work-root'):
        init.add_argument('--' + name, type=Path, required=True)
    init.add_argument('--function', required=True)
    init.add_argument('--parent-attempt-id', type=int, required=True)
    step = sub.add_parser('advance')
    step.add_argument('--state', type=Path, required=True)
    step.add_argument('--max-calls', type=int, default=0)
    step.add_argument('--proposal', type=Path)
    step.add_argument('--parent-version', type=int)
    step.add_argument('--model', default='gpt-oss:20b')
    step.add_argument('--endpoint', default='http://127.0.0.1:11435')
    step.add_argument('--model-lock', type=Path)
    step.add_argument('--seed', type=int, default=1)
    step.add_argument('--think', choices=('low', 'medium', 'high'), default='low')
    args = vars(parser.parse_args())
    command = args.pop('command')
    if command == 'init':
        args['source'] = args['source'].read_text()
        result = initialize(**args)
    else:
        args['state_path'] = args.pop('state')
        args['proposal'] = json.loads(args['proposal'].read_bytes()) if args['proposal'] else None
        result = advance(**args)
    print(json.dumps({k: result.get(k) for k in ('status', 'complete', 'current', 'error')}))


if __name__ == '__main__':
    main()
