"""Finite, opt-in fixed versus accumulating differential panels.

``compare_panels`` returns target/source digests, admitted and rejected case
counts, and ``fixed``/``accumulating`` panels. Each panel has a content
``version``, ordered ``ranking`` of candidate IDs, and per-ID receipts with
``passed``, ``failed``, ``inconclusive``, ``checked`` (passed + failed), and
per-case observations. Passing these finite synthetic cases is not a semantic
or object-match certificate.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from solver import mips_differential as differential


MAX_BASE_CASES = 64
MAX_EXTRA_CASES = 8
MAX_CANDIDATES = 32


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _case(payload: dict) -> differential.TestCase:
    if not isinstance(payload, dict):
        raise ValueError('case must be a TestCase-compatible object')
    fields = set(differential.TestCase.__dataclass_fields__)
    if set(payload) - fields:
        raise ValueError('unsupported case field')
    if not isinstance(payload.get('name'), str) or not payload['name']:
        raise ValueError('case name must be a nonempty string')
    seed = payload.get('seed')
    if type(seed) is not int or not 0 <= seed <= 0xffffffff:
        raise ValueError('case seed must be a uint32')
    normalized = {'name': payload['name'], 'seed': seed}
    for field, arity in (('player_writes', 3), ('global_writes', 3),
                         ('entry_registers', 2), ('call_returns', 3)):
        rows = payload.get(field, ())
        if not isinstance(rows, (list, tuple)) or any(
                not isinstance(row, (list, tuple)) or len(row) != arity
                for row in rows):
            raise ValueError(f'{field} must contain {arity}-item rows')
        normalized[field] = tuple(tuple(row) for row in rows)
    return differential.TestCase(**normalized)


def _candidate(row: dict) -> dict:
    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
        raise ValueError('candidate id must be a nonempty string')
    if not isinstance(row.get('source'), str) or not row['source']:
        raise ValueError('candidate source must be a nonempty string')
    if not isinstance(row.get('assembly'), str) or not row['assembly']:
        raise ValueError('candidate assembly must be a nonempty string')
    source_sha256 = _digest(row['source'])
    assembly_sha256 = _digest(row['assembly'])
    for field, actual in (('source_sha256', source_sha256),
                          ('assembly_sha256', assembly_sha256)):
        if field in row and row[field] != actual:
            raise ValueError(f'candidate {row["id"]} {field} does not match content')
    return {**row, 'source_sha256': source_sha256,
            'assembly_sha256': assembly_sha256}


def _version(function: str, target_sha256: str, cases: list,
             max_steps: int) -> str:
    payload = {'function': function, 'target_sha256': target_sha256,
               'interpreter_sha256': _interpreter_identity(),
               'max_steps': max_steps, 'cases': [asdict(case) for case in cases]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _interpreter_identity():
    from solver import cfg
    data = {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (differential, cfg)}
    return _digest(json.dumps(data, sort_keys=True))


def _panel(function: str, target: differential.Program, target_sha256: str,
           candidates: list[dict], cases: list, max_steps: int) -> dict:
    version = _version(function, target_sha256, cases, max_steps)
    results = {}
    for candidate in candidates:
        program = differential.Program.parse(candidate['id'], candidate['assembly'])
        observations = []
        for case in cases:
            comparison = differential.compare_programs(
                target, program, case, max_steps=max_steps)
            observations.append({'case': case.name, 'status': comparison.status,
                                 'reasons': list(comparison.reasons),
                                 'target_status': comparison.target.status,
                                 'candidate_status': comparison.candidate.status})
        passed = sum(row['status'] == 'passed' for row in observations)
        failed = sum(row['status'] == 'failed' for row in observations)
        inconclusive = sum(row['status'] == 'inconclusive' for row in observations)
        results[candidate['id']] = {
            'source_sha256': candidate['source_sha256'],
            'assembly_sha256': candidate['assembly_sha256'],
            'panel_version': version,
            'passed': passed, 'failed': failed, 'inconclusive': inconclusive,
            'checked': passed + failed, 'cases': observations,
        }
    ranking = sorted(results, key=lambda cid: (
        results[cid]['failed'], results[cid]['inconclusive'],
        -results[cid]['passed']))
    return {'version': version, 'case_count': len(cases),
            'ranking': ranking, 'candidates': results}


def compare_panels(function, target_assembly, candidates, base_cases, extra_cases,
                   *, max_steps=1000) -> dict:
    """Compare one source-distinct candidate set on fixed and retained panels.

    At most 64 base cases, eight extra proposals, and 32 candidates are
    accepted. The target executes before either candidate panel. Only target
    completed cases without ABI violations are retained. ``checked`` counts
    decisive candidate observations; inconclusive executions never fail a
    candidate. ``base_pass_extra_checked`` counts base-passing candidates
    exposed to at least one decisive admitted extra comparison.
    """
    if not isinstance(function, str) or not function:
        raise ValueError('function must be a nonempty string')
    if not isinstance(target_assembly, str) or not target_assembly:
        raise ValueError('target assembly must be a nonempty string')
    if type(max_steps) is not int or not 0 < max_steps <= 10000:
        raise ValueError('max_steps must be between 1 and 10000')
    candidates, base_cases, extra_cases = (list(candidates), list(base_cases),
                                           list(extra_cases))
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError('candidate budget exceeded')
    if len(base_cases) > MAX_BASE_CASES:
        raise ValueError('base case budget exceeded')
    if len(extra_cases) > MAX_EXTRA_CASES:
        raise ValueError('extra case budget exceeded')
    normalized_candidates = [_candidate(row) for row in candidates]
    ids = [row['id'] for row in normalized_candidates]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate candidate id')
    base_cases = [_case(row) for row in base_cases]
    extra_cases = [_case(row) for row in extra_cases]
    names = [case.name for case in (*base_cases, *extra_cases)]
    if len(set(names)) != len(names):
        raise ValueError('duplicate case name')

    target = differential.Program.parse(function, target_assembly)
    admitted_base, admitted_extra, rejected = [], [], []
    for origin, group, retained in (('base', base_cases, admitted_base),
                                    ('extra', extra_cases, admitted_extra)):
        for case in group:
            run = differential.execute_case(target, case, max_steps=max_steps)
            if run.status in differential.COMPLETED_STATUSES and not run.abi_violations:
                retained.append(case)
            else:
                rejected.append({'case': case.name, 'origin': origin,
                                 'status': 'target_inconclusive',
                                 'target_status': run.status,
                                 'abi_violations': list(run.abi_violations),
                                 'reason': run.error or 'target ABI violation or incomplete execution'})

    target_sha256 = _digest(target_assembly)
    fixed = _panel(function, target, target_sha256, normalized_candidates,
                   admitted_base, max_steps)
    accumulating = _panel(function, target, target_sha256, normalized_candidates,
                          admitted_base + admitted_extra, max_steps)
    exposed = falsified = checked_comparisons = 0
    base_count = len(admitted_base)
    for cid in ids:
        base = fixed['candidates'][cid]
        if not base_count or base['failed'] or base['inconclusive'] or base['checked'] != base_count:
            continue
        extra_rows = accumulating['candidates'][cid]['cases'][base_count:]
        decisive = [row for row in extra_rows if row['status'] in ('passed', 'failed')]
        if decisive:
            exposed += 1
            checked_comparisons += len(decisive)
            falsified += any(row['status'] == 'failed' for row in decisive)
    return {'function': function, 'target_sha256': target_sha256,
            'interpreter_sha256': _interpreter_identity(),
            'retained_cases': [asdict(case) for case in admitted_base + admitted_extra],
            'admitted_base': len(admitted_base),
            'admitted_extra': len(admitted_extra),
            'rejected_cases': rejected,
            'fixed': fixed, 'accumulating': accumulating,
            'base_pass_extra_checked': exposed,
            'base_pass_extra_comparisons': checked_comparisons,
            'base_pass_extra_falsified': falsified,
            'scope': 'finite synthetic observations; no semantic or object certificate'}
