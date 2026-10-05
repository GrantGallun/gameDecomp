"""Bounded, source-bound assignment/use context for model repair.

This is a lexical inventory, not CFG/liveness or instruction-ownership proof.
Compiler line attribution and source syntax hypotheses remain distinguished.
"""
from __future__ import annotations

import json
import re

from solver import project_headers, regalloc_mutations as rm, residual_sites, scalar_coalesce


def infer_function(source):
    from solver.repair_context import definition
    masked = project_headers._mask_noncode(source)
    found = []
    for name in dict.fromkeys(re.findall(r'(?m)^\s*[\w *]+\b(\w+)\s*\(', masked)):
        if name in rm.KEYWORDS:
            continue
        try:
            definition(source, name)
            found.append(name)
        except ValueError:
            pass
    return found[0] if len(found) == 1 else None


def build(source, function, diff, *, attribution=None, history=(), max_occurrences=12):
    mapping = residual_sites.source_map(source, function, diff, attribution)
    # Prefer compiler-attributed regions. Fallback regions remain explicitly
    # hypothetical, and omitted inventories must not imply absence of hazards.
    sites = sorted(mapping['sites'], key=lambda s: s['evidence'] != 'direct-compiler-line')
    report = {key: mapping[key] for key in ('source_sha256', 'diff_sha256',
              'direct_attribution_status', 'instruction_ownership_proven')}
    report.update(scope='Lexical inventory, not CFG/liveness proof. Includes are not expanded; '
                  'header-only volatile qualifiers/macros and aliasing remain unresolved.',
                  sites=[{**s, 'mismatch_ids': s['mismatch_ids'][:12]} for s in sites[:6]],
                  omitted_sites=max(0, len(sites)-6), mismatches=mapping['mismatches'][:12],
                  omitted_mismatches=max(0, len(mapping['mismatches'])-12), locals=[])
    allowed = ('label', 'compiled', 'exact', 'gradient', 'weighted_score_before',
               'weighted_score_after', 'child_source_sha256', 'child_diff_sha256')
    observations = [row for row in history if row.get('parent_source_sha256') == report['source_sha256']
                    and row.get('parent_diff_sha256') == report['diff_sha256']]
    report['recent_observations'] = [{k: (v[:240] if isinstance(v, str) else v)
                                    for k, v in row.items() if k in allowed} for row in observations[-4:]]
    report['omitted_observations'] = max(0, len(observations)-4)
    try:
        begin, stop = rm._body(source, function)
        masked = project_headers._mask_noncode(source)
        cursor, declarations = begin, []
        while match := scalar_coalesce._DECL.match(masked, cursor, stop):
            declarations.append((match['name'], ' '.join(match['type'].split()), match.start('name')))
            cursor = match.end()
        toks = rm.tokens(masked[cursor:stop], cursor)
    except (ValueError, TypeError):
        report['inventory_status'] = 'unavailable'
        return report
    lines = source.splitlines()
    def location(offset):
        line = source.count('\n', 0, offset) + 1
        return {'line': line, 'excerpt': lines[line-1].strip()[:180]}
    barriers = []
    for i, tok in enumerate(toks):
        value = tok[1]
        kind = None
        if (tok[0] == 'ident' and i+1 < len(toks) and toks[i+1][1] == '('
                and value not in rm.KEYWORDS | rm.TYPE_WORDS):
            kind = 'call'
        elif value in {')', ']'} and i+1 < len(toks) and toks[i+1][1] == '(':
            kind = 'possible indirect call/cast'
        elif value in rm.ASSIGN:
            kind = 'write (aliasing unresolved)'
        elif value in {'&', 'volatile', '++', '--'}:
            kind = 'escape/qualifier/update'
        if kind:
            row = {'kind': kind, **location(tok[2])}
            if row not in barriers:
                barriers.append(row)
    locals_ = []
    for name, typ, offset in declarations:
        refs = [i for i, tok in enumerate(toks) if tok[0] == 'ident' and tok[1] == name
                and (not i or toks[i-1][1] not in {'.', '->'})]
        if not refs:
            continue
        occurrences = []
        for i in refs:
            following = toks[i+1][1] if i+1 < len(toks) else ''
            preceding = toks[i-1][1] if i else ''
            role = ('assignment' if following == '=' else 'read/write'
                    if following in rm.ASSIGN | {'++', '--'} or preceding in {'++', '--'} else 'read')
            occurrences.append({'role': role, **location(toks[i][2])})
        implicated = [s for s in report['sites'] if any(s['start'] <= toks[i][2] < s['stop'] for i in refs)]
        hazard = [b for b in barriers if occurrences[0]['line'] <= b['line'] <= occurrences[-1]['line']]
        cap = max(0, min(20, max_occurrences))
        # Keep context for the implicated later value even when earlier roles
        # consume the payload budget. Retain some earlier uses alongside it.
        anchors = [j for j, i in enumerate(refs) if any(s['start'] <= toks[i][2] < s['stop']
                    for s in implicated if s['evidence'] == 'direct-compiler-line')]
        priority = []
        for j in anchors:
            priority.extend(range(j, min(j+3, len(refs))))
        priority.extend(range(min(2, len(refs))))
        for j in anchors:
            priority.extend(range(max(0, j-2), j))
        priority.extend(range(len(refs)))
        selected = sorted(list(dict.fromkeys(priority))[:cap])
        locals_.append({'name': name, 'type': typ, 'declaration': location(offset),
                        'occurrences': [occurrences[j] for j in selected], 'omitted_occurrences': max(0, len(occurrences)-cap),
                        'barriers': hazard[:8], 'omitted_barriers': max(0, len(hazard)-8),
                        'priority': 0 if any(s['evidence'] == 'direct-compiler-line' for s in implicated)
                                    else 1 if implicated else 2})
    locals_.sort(key=lambda row: row['priority'])
    report['locals'] = [{k: v for k, v in row.items() if k != 'priority'} for row in locals_[:4]]
    report['omitted_locals'] = max(0, len(locals_)-4)
    report['inventory_status'] = 'leading scalar locals only; nested scopes are not resolved'
    return report


def render(source, function, diff, *, attribution=None, history=()):
    function = function or infer_function(source)
    if not function:
        return '\nASSIGNMENT-SCOPED DIAGNOSTIC PACKET: unavailable (ambiguous function).\n'
    return ('\nASSIGNMENT-SCOPED DIAGNOSTIC PACKET:\n'
            + json.dumps(build(source, function, diff, attribution=attribution, history=history), separators=(',', ':'))
            + '\nReason about each assignment and its uses, including earlier roles outside the diff. '
              'For a cached-field substitution, preserve conversions and check calls, intervening writes, '
              'aliasing and volatile access. Missing or omitted evidence is not proof of safety. '
              'State the predicted assembly effect in the existing hypothesis field; use the existing edit schema. '
              'Scores are search proxies; a hypothesis needs compiler verification.\n')
