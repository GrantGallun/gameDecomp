"""Opt-in model-authored tool laboratory; the trusted coordinator owns all verdicts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from eval import coverage
from eval.tool_sandbox import run_tool

AUTHOR_PROMPT = '''Build one reusable Python 3 repair tool from these recurring compiler failures.
The compiler is IDO 5.3 (MIPS, -O2). Each failure is C that compiles, but to different instructions than the
target object; `diff` shows target rows (-) and candidate rows (+).

Return JSON with exactly name, rationale, code. name must match [a-z][a-z0-9_]{0,63}.
code is a complete Python program. At run time it receives ONE observation object (one element of the examples
list below, NOT the list) on stdin, and must print exactly {"candidates": [full C source strings]}. Start from:

    import json, re, sys

    def propose(obs):
        # obs["source"], obs["diff"], obs["target_asm"], obs["candidate_asm"], obs["compiler_error"]
        candidates = []
        # ... read the diff, decide whether this residual is one your tool repairs, edit obs["source"] ...
        return candidates

    print(json.dumps({"candidates": propose(json.load(sys.stdin))[:8]}))

Each candidate is a complete, changed C translation unit in ordinary C89 (no operators C does not have).
Emit [] when the residual is not one your tool repairs. Python standard library only; no network, host files,
compiler or verifier calls. No directives, comments, string literals or assembly in emitted C. Preserve names
and unrelated behaviour. Never hardcode the examples: the same tool runs on unseen functions, including ones it
must decline. Only compiler-verified coverage matters. Explain in `rationale` which residual the tool repairs
and why your C change makes the compiler emit the target instruction.'''


def author_message(observations):
    """The first user turn: the examples, labelled so the one-object stdin contract cannot be misread."""
    return ('Examples of the recurring failure (your tool will receive ONE of these objects at a time):\n'
            + json.dumps(observations, indent=1))


def development_feedback(proposal_error, dev_results):
    """What the author may see about its own tool on DEVELOPMENT cases: per case, what the tool emitted and what the
    compiler said about each candidate (error text or instruction diff). Evaluation results never reach it."""
    if proposal_error:
        return {'proposal_error': proposal_error}
    cases = []
    for r in dev_results:
        fb = dict(r.get('tool_feedback') or {})
        row = {'id': r['id'], 'repaired': bool(r['exact']), 'tool_status': r.get('tool_status')}
        if fb.get('error') or fb.get('stderr'):
            row['tool_error'] = (fb.get('error') or '') + (fb.get('stderr') or '')[-800:]
        if fb.get('candidate_count') == 0 and r.get('tool_status') == 'ok':
            row['note'] = 'your tool ran but emitted no candidates for this observation'
        row['candidates'] = fb.get('candidates', [])
        cases.append(row)
    return {'development': cases}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_hash(source):
    return hashlib.sha256(source.encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


class Journal:
    def __init__(self, path):
        self.path = path
        self.sequence = 0

    def add(self, kind, **fields):
        self.sequence += 1
        row = {'seq': self.sequence, 'kind': kind, **fields}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return self.sequence


@dataclass(frozen=True)
class Limits:
    compiles: int = 8
    seconds: float = 30
    author_calls: int = 3

    def __post_init__(self):
        if type(self.compiles) is not int or not 2 <= self.compiles <= 64:
            raise ValueError('compile ceiling must be 2..64 per case, including confirmations')
        if not math.isfinite(self.seconds) or not 1 <= self.seconds <= 120:
            raise ValueError('per-case seconds must be 1..120')
        if type(self.author_calls) is not int or not 1 <= self.author_calls <= 6:
            raise ValueError('author call ceiling must be 1..6')


def validate_panel(cases):
    seen, families, sources = set(), {'dev': set(), 'eval': set()}, {'dev': set(), 'eval': set()}
    for c in cases:
        if not isinstance(c, dict) or set(c) != {'id', 'family', 'split', 'source', 'target'}:
            raise ValueError('panel rows require exactly id, family, split, source, target')
        if any(not isinstance(v, str) or not v for v in c.values()):
            raise ValueError('panel values must be nonempty strings')
        if c['id'] in seen:
            raise ValueError('duplicate case id')
        seen.add(c['id'])
        if c['split'] not in families:
            raise ValueError('split must be dev or eval')
        families[c['split']].add(c['family'])
        sources[c['split']].add(c['source'])
    if not all(families.values()) or families['dev'] & families['eval']:
        raise ValueError('nonempty, family-disjoint development and evaluation panels required')
    if sources['dev'] & sources['eval']:
        raise ValueError('identical source crosses development/evaluation boundary')


def validate_tool(value):
    if not isinstance(value, dict) or set(value) != {'name', 'rationale', 'code'}:
        raise ValueError('author must return exactly name, rationale, code')
    if not isinstance(value['name'], str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', value['name']):
        raise ValueError('invalid tool name')
    for key, limit in [('code', 48000), ('rationale', 4000)]:
        if not isinstance(value[key], str) or not 1 <= len(value[key].encode()) <= limit:
            raise ValueError(f'invalid {key} length')
    return {**value, 'sha256': digest(value)}


def observation(source, receipt):
    return {'source': source, 'diff': receipt.get('diff', '')[:24000],
            'target_asm': receipt.get('target_asm', '')[:24000],
            'candidate_asm': receipt.get('candidate_asm', '')[:24000],
            'compiler_error': receipt.get('stderr', '')[-3000:]}


def baseline_candidates(obs):
    from solver.rewrites import propose
    return list(dict.fromkeys(r(obs['source']) for r in propose(obs['source'], obs['diff'])))[:64]


def decide(before, after, *, development, caps, motivating):
    ids = [r['id'] for r in before]
    complete = (len(set(ids)) == len(ids) and len(after) == len(ids)
                and {r['id'] for r in after} == set(ids)
                and all(r['status'] == 'complete' for r in [*before, *after]))
    b, a = ({r['id'] for r in rows if r['exact']} for rows in (before, after))
    total = {k: sum(r[k] for r in after) + development[k] for k in ('compiles', 'seconds')}
    baseline = {k: sum(r[k] for r in before) for k in total}
    within = all(math.isfinite(total[k]) and 0 <= total[k] <= caps[k] for k in total)
    cheaper = all(total[k] <= baseline[k] for k in total) and any(total[k] < baseline[k] for k in total)
    reason = ('incomplete or infrastructure-failed panel' if not complete else
              'lost covered cases' if b - a else 'total budget exceeded including development' if not within else
              'no motivating development repair' if not motivating else
              'additional coverage' if a - b else 'same coverage, lower total cost' if cheaper else 'no net gain')
    return {'retain': reason in ('additional coverage', 'same coverage, lower total cost'),
            'reason': reason, 'gained': sorted(a - b), 'lost': sorted(b - a),
            'baseline_cost': baseline, 'tool_total_cost': total, 'budget': caps}


def run_case(case, oracle, journal, *, arm, limits, tool=None, baseline=baseline_candidates):
    start = time.monotonic()
    deadline = start + limits.seconds
    result = {'id': case['id'], 'exact': False, 'status': 'complete', 'compiles': 0,
              'seconds': 0.0, 'tool_status': 'unused', 'training': None}
    observed = None
    attempts = []

    def score(source, action):
        started_event = journal.add('compile_start', case=case['id'], split=case['split'],
                                    arm=arm, action=action, source_sha256=source_hash(source),
                                    parent_event=attempts[0] if attempts else None)
        try:
            receipt = oracle.score(case, source, deadline=deadline)
            if receipt.get('source_sha256') != source_hash(source):
                receipt = {**receipt, 'status': 'infra_error', 'exact': False,
                           'error': 'oracle source identity mismatch'}
        except Exception as exc:
            receipt = {'status': 'infra_error', 'exact': False, 'compiled': False,
                       'compiles': 0, 'error': f'{type(exc).__name__}: {exc}'}
        result['compiles'] += receipt.get('compiles', 0)
        event = journal.add('compile', case=case['id'], split=case['split'], arm=arm,
                            action=action, source=source, source_sha256=source_hash(source),
                            start_event=started_event, receipt=receipt)
        attempts.append(event)
        if receipt['status'] == 'infra_error':
            result['status'] = 'infra_error'
        return receipt, event

    def available():
        return result['compiles'] < limits.compiles and time.monotonic() < deadline

    def confirmed(source, receipt, action):
        if not receipt.get('exact') or not available():
            return False
        other, _ = score(source, 'confirm:' + action)
        return bool(other.get('exact') and other.get('certificate', {}).get('exact'))

    try:
        root, _ = score(case['source'], 'root')
        observed = observation(case['source'], root)
        if confirmed(case['source'], root, 'root'):
            result['exact'] = True
        elif result['status'] == 'complete':
            proposed = []
            if tool and available():
                executed = run_tool(tool['code'], observed, timeout=min(3, deadline - time.monotonic()))
                result['tool_status'] = executed['status']
                result['tool_feedback'] = {k: executed[k] for k in ('status', 'error', 'stderr') if k in executed}
                result['tool_feedback']['candidate_count'] = len(executed['candidates'])
                journal.add('tool', case=case['id'], split=case['split'], arm=arm,
                            tool_sha256=tool['sha256'], observation=observed, receipt=executed)
                proposed.extend((source, 'tool') for source in executed['candidates'])
            try:
                proposed.extend((source, 'baseline') for source in baseline(observed))
            except Exception as exc:
                journal.add('baseline_error', case=case['id'], arm=arm, error=str(exc))
                result['status'] = 'infra_error'
            seen = {case['source']}
            for source, action in proposed:
                if not available() or result['status'] != 'complete':
                    break
                if source in seen:
                    continue
                seen.add(source)
                child, _ = score(source, action)
                if action == 'tool' and 'tool_feedback' in result:
                    # Per-candidate compiler outcome, for development feedback (development_feedback filters by split).
                    result['tool_feedback'].setdefault('candidates', []).append({
                        'source': source[:600], 'compiled': bool(child.get('compiled')),
                        'exact': bool(child.get('exact')),
                        **({'compiler_error': (child.get('stderr') or child.get('error') or '')[-600:]}
                           if not child.get('compiled') else {'diff': (child.get('diff') or '')[:1200]})})
                if confirmed(source, child, action):
                    result['exact'] = True
                    if action == 'tool' and case['split'] == 'dev':
                        result['training'] = {
                            'id': case['id'] + ':' + tool['sha256'], 'kind': 'verified_tool_use',
                            'split': 'train', 'family': case['family'], 'function': case['id'],
                            'label_source': 'development compiler certificate and independent rebuild',
                            'messages': [{'role': 'system', 'content': 'Choose a repair tool. Available tool: ' +
                                         json.dumps(tool)}, {'role': 'user', 'content': json.dumps(observed)}],
                            'completion': json.dumps({'tool': tool['name'], 'arguments': {}}),
                            'tool_sha256': tool['sha256'], 'receipt_events': list(attempts)}
                    break
    except Exception as exc:
        result.update(status='infra_error', error=f'{type(exc).__name__}: {exc}')
        journal.add('case_error', case=case['id'], arm=arm, error=result['error'])
    result['seconds'] = time.monotonic() - start
    if result['seconds'] > limits.seconds or result['compiles'] > limits.compiles:
        result.update(status='budget_exceeded', exact=False, training=None)
    result['attempt_events'] = attempts
    journal.add('case_complete', case=case['id'], split=case['split'], arm=arm,
                result={k: v for k, v in result.items() if k != 'training'})
    return result


def run_experiment(cases, propose, oracle, output: Path, *, limits=Limits(), baseline=baseline_candidates):
    validate_panel(cases)
    output.mkdir(parents=True, exist_ok=False)
    journal = Journal(output / 'events.jsonl')
    dev = [c for c in cases if c['split'] == 'dev']
    exam = [c for c in cases if c['split'] == 'eval']
    frozen = {'cases': cases, 'limits': vars(limits),
              'targets': {c['id']: hashlib.sha256(Path(c['target']).read_bytes()).hexdigest() for c in cases},
              'implementation': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                 (Path(__file__), Path(__file__).with_name('tool_sandbox.py'),
                                  Path(__file__).with_name('tool_learning_oracle.py'))}}
    write_json(output / 'manifest.json', frozen)
    journal.add('freeze_panel', manifest_sha256=digest(frozen))
    development = {'compiles': 0, 'seconds': 0.0, 'tokens': 0}
    dev_start = time.monotonic()
    observations, failed_dev = [], set()
    for case in dev:
        started_event = journal.add('compile_start', case=case['id'], split='dev',
                                    arm='discovery', action='root', source_sha256=source_hash(case['source']))
        try:
            receipt = oracle.score(case, case['source'], deadline=time.monotonic() + limits.seconds)
            if receipt.get('source_sha256') != source_hash(case['source']):
                receipt = {**receipt, 'status': 'infra_error', 'exact': False,
                           'error': 'oracle source identity mismatch'}
        except Exception as exc:
            receipt = dict(status='infra_error', compiled=False, exact=False, compiles=0, error=str(exc))
        development['compiles'] += receipt.get('compiles', 0)
        journal.add('compile', case=case['id'], split='dev', arm='discovery', action='root',
                    source=case['source'], source_sha256=source_hash(case['source']),
                    start_event=started_event, receipt=receipt)
        if receipt.get('compiled') and not receipt.get('exact') and receipt['status'] == 'ok':
            observations.append(observation(case['source'], receipt))
            failed_dev.add(case['id'])
    messages = [{'role': 'system', 'content': AUTHOR_PROMPT},
                {'role': 'user', 'content': author_message(observations)}]
    tool, proposal_error = None, None
    dev_results, motivating = [], False
    for round_number in range(limits.author_calls):
        tool, proposal_error = None, None
        journal.add('author_request', round=round_number, messages=messages)
        try:
            if len(observations) < 2:
                raise ValueError('need at least two observed development failures')
            value, cost = propose(messages)
            journal.add('author_response', round=round_number, value=value, cost=cost)
            development['tokens'] += cost['tokens']
            messages.append({'role': 'assistant', 'content': value if isinstance(value, str) else json.dumps(value)})
            # Charge raw response metadata before parsing model-controlled JSON.
            if isinstance(value, str):
                value = json.loads(value)
            tool = validate_tool(value)
            write_json(output / f'proposal-{round_number}.json', tool)
        except Exception as exc:
            proposal_error = f'{type(exc).__name__}: {exc}'
            journal.add('author_error', round=round_number, error=proposal_error)
        # Development measures the TOOL: the ordinary rewrite candidates would only spend development compiles
        # (charged against the tool arm's budget) without telling the author anything about its tool.
        dev_results = [run_case(c, oracle, journal, arm=f'development:{round_number}', limits=limits,
                                tool=tool, baseline=lambda _obs: []) for c in dev] if tool else []
        development['compiles'] += sum(r['compiles'] for r in dev_results)
        repaired = {r['id'] for r in dev_results if r['training'] and r['id'] in failed_dev}
        motivating = bool(repaired)                       # retention still needs at least one (decide())
        # Keep revising until EVERY observed development failure is repaired: stopping at the first success froze a
        # tool fitted to one statement shape (v4, 2026-10-04).
        if repaired >= failed_dev:
            break
        feedback = development_feedback(proposal_error, dev_results)
        messages.append({'role': 'user', 'content': 'Development feedback only:\n' + json.dumps(feedback, indent=1) +
                         '\nRevise the tool: read each candidate\'s compiler error or remaining diff, then change '
                         'the C edit itself, not only the guards. Return the complete JSON again.'})
    if tool:
        write_json(output / 'frozen-tool.json', tool)
    journal.add('freeze_tool', tool_sha256=tool['sha256'] if tool else None)
    development['seconds'] = time.monotonic() - dev_start
    before, after = [], []
    for index, case in enumerate(exam):
        # Alternate ordering to reduce warm-cache bias. States/compilers are fresh.
        arms = [('baseline', None), ('tool', tool)]
        for arm, candidate in arms[::1 if index % 2 == 0 else -1]:
            row = run_case(case, oracle, journal, arm=arm, limits=limits, tool=candidate, baseline=baseline)
            (before if arm == 'baseline' else after).append(row)
    before.sort(key=lambda r: r['id'])
    after.sort(key=lambda r: r['id'])
    caps = {'compiles': limits.compiles * len(exam), 'seconds': limits.seconds * len(exam)}
    decision = decide(before, after, development=development, caps=caps, motivating=motivating)
    integrity_error = None
    try:
        oracle.check_integrity()
        for c in cases:
            if hashlib.sha256(Path(c['target']).read_bytes()).hexdigest() != frozen['targets'][c['id']]:
                raise RuntimeError('target identity changed')
        for name, expected in frozen['implementation'].items():
            if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != expected:
                raise RuntimeError('experiment implementation changed')
    except Exception as exc:
        integrity_error = str(exc)
        decision.update(retain=False, reason='integrity failure; all affected results quarantined')
        for row in before + after:
            row['prior_exact'] = row['exact']
            row.update(status='quarantined', exact=False, training=None)
        decision.update(gained=[], lost=[])
        journal.add('quarantine', error=integrity_error, cases=[c['id'] for c in cases])
    if any(r['status'] != 'complete' for r in dev_results) or proposal_error:
        decision.update(retain=False, reason='incomplete development or proposal')
    panel = {'ids': [c['id'] for c in exam], 'classes': {c['id']: c['family'] for c in exam},
             'sha256': digest(frozen)}
    comparison = coverage.compare(panel, before, after)
    report = {'scope': 'laboratory object coverage; not game matches or whole-ROM coverage',
              'manifest_sha256': digest(frozen), 'tool_sha256': tool['sha256'] if tool else None,
              'proposal_error': proposal_error, 'integrity_error': integrity_error,
              'development': development, 'evaluation': comparison, 'decision': decision,
              'coverage_before': coverage.coverage(panel, before),
              'coverage_after': coverage.coverage(panel, after), 'before': before, 'after': after,
              'all_compiles': development['compiles'] + sum(r['compiles'] for r in before + after),
              'weights_updated': False, 'production_activated': False}
    with (output / 'development-sft.jsonl').open('w', encoding='utf-8') as stream:
        if not integrity_error:
            for row in dev_results:
                if row['training'] and row['status'] == 'complete':
                    stream.write(json.dumps(row['training']) + '\n')
    if decision['retain']:
        (output / 'library').mkdir()
        write_json(output / 'library' / 'tool.json', {**tool, 'evaluation': decision})
    journal.add('decision', decision=decision)
    write_json(output / 'report.json', report)
    return report
