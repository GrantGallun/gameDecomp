"""Opt-in readability proposals. Only fresh, source-bound certificates admit edits."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Callable

from solver import pointer_spill_cleanup, project_headers, repair_context


def digest(source: str) -> str:
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Proposal:
    kind: str
    reason: str
    parent_sha256: str
    source: str


def proposals(source: str, function: str) -> list[Proposal]:
    """Bounded grammar, not a C refactoring engine; the compiler still decides."""
    definition, end = repair_context.definition(source, function)
    masked = project_headers._mask_noncode(source)
    body = masked[definition.end():end - 1]
    if re.search(r'(?m)^\s*#', body) or re.search(r'\\\r?\n', source):
        return []
    result = []
    cleaned = pointer_spill_cleanup.propose(source, function)
    if cleaned['source'] != source:
        result.append(Proposal('dead_pointer_copies',
            'Remove locals used only as destinations of plain pointer copies.',
            digest(source), cleaned['source']))
    # Only top-level, standalone pointer declarations with generated names.
    # Nested scopes are deliberately excluded to avoid renaming shadowed locals.
    if '{' in body or '}' in body:
        return result
    declarations = re.finditer(
        r'(?m)^[ \t]*(?:struct\s+)?([A-Z][A-Za-z0-9_]*)\s*\*\s*'
        r'(temp_[A-Za-z0-9_]+)\s*;', body)
    for declaration in declarations:
        type_name, old = declaration.group(1, 2)
        new = type_name[0].lower() + type_name[1:]
        if new == type_name or re.search(r'\b' + re.escape(new) + r'\b', masked):
            continue
        uses = list(re.finditer(r'\b' + re.escape(old) + r'\b', body))
        # Ambiguous member names, labels and macro definitions are declined.
        if any(re.search(r'(?:\.|->)\s*$', body[:m.start()])
               or re.match(r'\s*:', body[m.end():]) for m in uses):
            continue
        if re.search(r'(?m)^\s*#\s*define\b[^\n]*\b(?:' +
                     re.escape(old) + '|' + re.escape(new) + r')\b', masked):
            continue
        candidate = source
        for use in reversed(uses):
            start, stop = definition.end() + use.start(), definition.end() + use.end()
            candidate = candidate[:start] + new + candidate[stop:]
        result.append(Proposal('type_derived_local_name',
            f'Rename {old} to {new}, derived from its declared {type_name} type.',
            digest(source), candidate))
    return result


def _certificate(attempt, source: str) -> dict | None:
    cert = attempt.verification or {}
    if (attempt.compiled and attempt.exact
            and (attempt.frontend or {}).get('passed') is True
            and cert.get('kind') == 'mips_object_section_certificate'
            and cert.get('exact') is True
            and cert.get('candidate_source_sha256') == digest(source)
            and cert.get('target_sha256') and cert.get('scope')
            and cert.get('build_inputs')):
        return cert
    return None


def clean(source: str, function: str, evaluate: Callable, *, max_attempts: int = 12,
          checkpoint: Callable | None = None) -> dict:
    """Evaluate baseline and one edit at a time; never promote a weaker match.

    evaluate(source, parent_receipt_id) returns workspace.Attempt. It must compile
    in an isolated workspace. No model, game source or campaign mutation here.
    """
    if max_attempts < 0:
        raise ValueError('max_attempts must be nonnegative')
    report = {'schema_version': 1, 'function': function, 'status': 'running',
              'original_sha256': digest(source), 'best_source': source,
              'best_sha256': digest(source), 'attempts': [], 'accepted': 0,
              'integration_requested': False}

    def save():
        if checkpoint:
            checkpoint(report)

    save()
    try:
        baseline = evaluate(source, None)
        report['baseline'] = asdict(baseline)
        reference = _certificate(baseline, source)
        if reference is None:
            report['status'] = 'baseline_not_verified_exact'
            save()
            return report
        parent = baseline.receipt_id
        seen = {digest(source)}
        while len(report['attempts']) < max_attempts:
            choices = [p for p in proposals(report['best_source'], function)
                       if digest(p.source) not in seen]
            if not choices:
                break
            proposal = choices[0]
            if proposal.parent_sha256 != report['best_sha256']:
                raise ValueError('stale cleanup proposal')
            seen.add(digest(proposal.source))
            attempt = evaluate(proposal.source, parent)
            cert = _certificate(attempt, proposal.source)
            accepted = bool(cert and all(cert.get(k) == reference.get(k)
                            for k in ('target_sha256', 'scope', 'build_inputs')))
            report['attempts'].append({'proposal': asdict(proposal),
                'result': asdict(attempt), 'accepted': accepted,
                'reason': 'exact_certificate_preserved' if accepted else
                          'exact_frontend_source_or_environment_gate_failed'})
            if accepted:
                report.update(best_source=proposal.source,
                              best_sha256=digest(proposal.source))
                report['accepted'] += 1
                parent = attempt.receipt_id
            save()
        report['status'] = 'complete'
    except Exception as exc:
        report.update(status='stopped_on_error', error=f'{type(exc).__name__}: {exc}')
    save()
    return report
