"""Candidate-only compiler interventions for campaign repair prompts.

Keep the original source intact: spans and line numbers refer to that source,
not a CGenerator rendering. These measurements are advisory, never edit guards.
Every probe in measure() goes through workspace.score with durable parentage.
"""
from __future__ import annotations

import collections
import hashlib
import importlib.util
import json
import re
import time
from pathlib import Path

from solver import code_shapes, line_map, regalloc_mutations, source_attribution, workspace

PROBE = '__gamedecomp_attr_probe'


def _structure(source, function):
    """Balanced statement spans, including unbraced bodies; unsupported labels decline."""
    body = code_shapes._body(source, function)
    if body is None:
        raise ValueError('no balanced, directive-free function body')
    masked, begin, end = body
    toks = regalloc_mutations.tokens(masked[begin - 1:end + 1], begin - 1)
    spans, points = [], set()

    def group(i, left, right):
        if toks[i][1] != left:
            raise ValueError('expected balanced group')
        depth = 1
        i += 1
        while i < len(toks) and depth:
            depth += (toks[i][1] == left) - (toks[i][1] == right)
            i += 1
        if depth:
            raise ValueError('unbalanced group')
        return i

    def statement(i, in_block=False):
        start, word = i, toks[i][1]
        if word in {'case', 'default'} or (i + 1 < len(toks) and toks[i + 1][1] == ':'):
            raise ValueError('label or switch entry: intervention topology unknown')
        if word == '{':
            points.add((toks[i][3], None))
            i += 1
            while i < len(toks) and toks[i][1] != '}':
                i = statement(i, True)
                points.add((toks[i - 1][3], None))
            if i >= len(toks):
                raise ValueError('unclosed block')
            i += 1
        elif word in {'if', 'for', 'while', 'switch'}:
            i = group(i + 1, '(', ')')
            i = statement(i)
            if word == 'if' and i < len(toks) and toks[i][1] == 'else':
                i = statement(i + 1)
        elif word == 'do':
            i = statement(i + 1)
            if i >= len(toks) or toks[i][1] != 'while':
                raise ValueError('incomplete do loop')
            i = group(i + 1, '(', ')')
            if toks[i][1] != ';':
                raise ValueError('incomplete do loop')
            i += 1
        else:
            while i < len(toks) and toks[i][1] != ';':
                if toks[i][1] in {'(', '['}:
                    i = group(i, toks[i][1], ')' if toks[i][1] == '(' else ']')
                elif toks[i][1] in {'{', '}'}:
                    raise ValueError('unsupported statement or initializer')
                else:
                    i += 1
            if i >= len(toks):
                raise ValueError('unterminated statement')
            i += 1
        a, b = toks[start][2], toks[i - 1][3]
        if word != '{':
            text = masked[a:b]
            # Opaque typedefs are conservatively treated as declarations too.
            declaration = word in regalloc_mutations.TYPE_WORDS or (word not in regalloc_mutations.KEYWORDS and bool(re.match(
                r'^[A-Za-z_]\w*\s+(?:\*\s*)*[A-Za-z_]\w*\s*(?:[;=\[])', text))
            )
            if not declaration:
                spans.append((a, b))
                if not in_block:
                    points.add((a, (a, b)))  # wrap an unbraced body for the probe only
                    points.add((b, (a, b)))
        return i

    if statement(0) != len(toks):
        raise ValueError('trailing tokens outside function body')
    return sorted(set(spans)), sorted(points, key=lambda p: (p[0], p[1] or (-1, -1)))


def analyze(source, function, target, listing, diff, attribution, compile_candidate, *, max_probes=96):
    packet = {'status': 'unavailable', 'source_sha256': source_attribution.sha(source),
              'diff_sha256': source_attribution.sha(diff), 'direct': [], 'regions': [],
              'declarations': [], 'missing': [], 'nonlocal': None, 'line_table': [],
              'skipped': {}, 'probes': []}
    skipped = collections.Counter()
    if (not attribution or attribution.get('status') != 'verified'
            or attribution.get('source_sha256') != packet['source_sha256']
            or attribution.get('diff_sha256') != packet['diff_sha256']):
        packet['reason'] = 'candidate line evidence unavailable or stale'
        return packet
    records = source_attribution.instructions_of(attribution)
    if ([r.get('normalized_line') for r in records] != list(range(1, len(listing) + 1))):
        packet['reason'] = 'line evidence does not cover the candidate listing'
        return packet
    n_lines = len(source.splitlines())
    lines = [r.get('candidate_line') for r in records]
    if any(n is not None and (not isinstance(n, int) or not 1 <= n <= n_lines) for n in lines):
        packet['reason'] = 'invalid candidate line number'
        return packet
    lm = line_map.LineMap(lines)
    att = lm.attribute(target, listing)
    # Include differing rows without a line record in intervention coverage.
    differing = line_map._changed_rows(listing, target)
    packet.update({'status': 'measured', 'line_table': sorted(att['changed']),
                   'nonlocal': line_map.nonlocal_kind(target, listing),
                   'unattributed': att['unattributed'], 'gap_spans': att['gap_spans']})

    def probe(code, label):
        if len(packet['probes']) >= max_probes:
            skipped['probe-limit'] += 1
            return None
        packet['probes'].append({'label': label, 'source_sha256': source_attribution.sha(code)})
        value = compile_candidate(code, label)
        if value is None:
            kind = 'probe' if label.startswith('probe-insert') else label.split(':')[0]
            skipped[kind + '-rejected-by-compiler'] += 1
        return value

    def line(offset):
        return source.count('\n', 0, offset) + 1

    try:
        spans, points = _structure(source, function)
    except (ValueError, IndexError) as exc:
        skipped['unsupported-source-topology'] += 1
        packet['reason'] = str(exc)
        spans, points = [], []
    influence = []
    for a, b in spans:
        # A null statement preserves unbraced control flow; original line topology is unchanged.
        blank = ';' + re.sub(r'[^\n]', ' ', source[a + 1:b])
        changed = probe(source[:a] + blank + source[b:], f'deletion:{a}-{b}')
        if changed is not None:
            rows = line_map._changed_rows(listing, changed)
            if rows:
                influence.append((line(a), line(b - 1), rows))
            else:
                skipped['deletion-changes-nothing'] += 1
    packet['regions'] = line_map.regions_for(differing, influence)
    packet['direct'] = sorted(n for n, rows in att['changed'].items() if any(
        a <= n <= b and set(rows) & infl for a, b, infl in influence))
    # Existing bounded local retyping proposals: the compiler, not a type table, measures influence.
    try:
        for label, _family, code in regalloc_mutations.local_types(source, function, limit=24):
            changed = probe(code, 'retype:' + label)
            if changed is not None and line_map._changed_rows(listing, changed) & differing:
                from solver import edit_locality
                packet['declarations'].extend(edit_locality.edited_lines(source, code))
    except ValueError:
        skipped['retype-source-unavailable'] += 1
    packet['declarations'] = sorted(set(packet['declarations']))
    probes, selected_sites = [], {}
    if att['gap_spans']:
        if re.search(r'\b' + PROBE + r'\b', source):
            skipped['probe-symbol-collision'] += 1
        else:
            for offset, wrap in points:
                marker = f' {PROBE} = 1; '
                if wrap:
                    a, b = wrap
                    body = source[a:offset] + marker + source[offset:b]
                    code = source[:a] + '{ ' + body + ' }' + source[b:]
                else:
                    code = source[:offset] + marker + source[offset:]
                code = f'extern volatile int {PROBE};\n' + code
                changed = probe(code, f'probe-insert:{offset}')
                if changed is None:
                    continue
                mentions = [i for i, row in enumerate(changed) if PROBE in row]
                if not mentions:
                    skipped['probe-store-not-found'] += 1
                    continue
                # The offset is authoritative when several statements share a source line.
                after = line(offset) if offset and source[offset - 1] != '\n' else line(offset) - 1
                probes.append({'after_line': after, 'offset': offset, 'wrap': wrap,
                               'line': line(offset),
                               'target_row': line_map._base_position(target, changed, mentions[-1])})
            for span in att['gap_spans']:
                def distance(probe):
                    pos, (lo, hi) = probe['target_row'], span
                    return 0 if lo <= pos < hi else min(abs(pos - lo), abs(pos - hi))
                if probes:
                    best = min(map(distance, probes))
                    for probe in probes:
                        if distance(probe) == best:
                            selected_sites[(probe['offset'], probe['wrap'])] = probe
    packet['insertion_sites'] = list(selected_sites.values())
    packet['missing'] = sorted({p['after_line'] for p in selected_sites.values()})
    packet['skipped'] = dict(skipped)
    return packet


def render(packet, source, diff):
    if (not packet or packet.get('source_sha256') != source_attribution.sha(source)
            or packet.get('diff_sha256') != source_attribution.sha(diff)):
        return ''
    if packet['status'] != 'measured':
        return '\nCOMPILER LOCALIZATION: unavailable (' + packet.get('reason', 'unknown') + ').\n'
    block = line_map.render_localization(packet)
    # Compiler influence is an observation, not exclusive ownership or a diagnosed fix.
    return ('\nCOMPILER LOCALIZATION (current candidate only; advisory):\n' + block
            + '\nThese interventions changed instructions; this does not prove exclusive ownership or the correct edit. '
              'You may edit elsewhere. Preserve the existing edit schema.\n'
            + 'Insertion sites (original source character offsets): ' + json.dumps(packet.get('insertion_sites', []))
            + '\nSkipped/unknown interventions: ' + json.dumps(packet['skipped'], sort_keys=True) + '\n')


def measure(repo, ws, function, parent, *, conn, run_id, run_config, max_probes=96):
    """Bind evidence to the live object; log probes and return eligible repair children separately."""
    unavailable = {'status': 'unavailable', 'source_sha256': source_attribution.sha(parent.source),
                   'diff_sha256': source_attribution.sha(parent.attempt.diff or ''),
                   'reason': 'compiled source-bound object and durable attempt ledger required'}
    evidence = parent.attempt.source_attribution or {}
    if conn is None or parent.attempt.receipt_id is None or parent.object_path is None or not parent.attempt.compiled:
        return unavailable, []
    obj = Path(parent.object_path)
    try:
        if hashlib.sha256(obj.read_bytes()).hexdigest() != evidence.get('candidate_object_sha256'):
            return {**unavailable, 'reason': 'candidate object does not match attribution'}, []
        listing = obj.with_name(obj.stem + '_object_dump_normalized.s').read_text().splitlines()
        captured = obj.with_name(obj.stem + '.source-lines.dump').read_text()
        if source_attribution.sha(captured) != evidence.get('line_dump_sha256'):
            return {**unavailable, 'reason': 'captured candidate dump changed'}, []
        normalizer = ws / 'objdump.py'
        if hashlib.sha256(normalizer.read_bytes()).hexdigest() != evidence.get('normalizer_sha256'):
            return {**unavailable, 'reason': 'candidate normalizer changed'}, []
        _records, raw = source_attribution.parse_dump(captured)
        spec = importlib.util.spec_from_file_location('_localization_normalizer', normalizer)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.process_objdump_lines(raw) != listing:
            return {**unavailable, 'reason': 'normalized candidate dump differs from captured object'}, []
        target = (ws / 'target_object_dump_normalized.s').read_text().splitlines()
    except OSError as exc:
        return {**unavailable, 'reason': str(exc)}, []
    children, receipts = [], []

    def compile_candidate(code, label):
        tag = f'{function}_localization_{time.time_ns()}'
        att = workspace.score(ws, repo, tag, code, conn=conn, func=function,
            strategy='compiler-localization:' + label.split(':')[0], model='zero-model',
            run_id=run_id, parent_attempt_id=parent.attempt.receipt_id,
            relation='compiler-localization', action=label, run_kind='model-repair', run_config=run_config,
            extra={'parent_source_sha256': source_attribution.sha(parent.source),
                   'diagnostic_only': label.startswith('probe-insert'),
                   'training_eligible': False, 'purpose': 'candidate-only attribution intervention'})
        if att.receipt_id is None:
            raise RuntimeError('localization compiler attempt was not logged')
        receipts.append(att.receipt_id)
        candidate_obj = ws / (tag + '.o')
        if att.compiled and not label.startswith('probe-insert') and workspace.repair_complete(att):
            from solver.modelrepair import CandidateState
            children.append(CandidateState(code, att, candidate_obj,
                            parent.labels + (label,), parent.kinds + ('compiler-localization',)))
        dump = ws / (tag + '_object_dump_normalized.s')
        return dump.read_text().splitlines() if att.compiled and dump.is_file() else None

    packet = analyze(parent.source, function, target, listing, parent.attempt.diff or '', evidence,
                     compile_candidate, max_probes=max_probes)
    packet.update(candidate_object_sha256=evidence.get('candidate_object_sha256'),
                  parent_attempt_id=parent.attempt.receipt_id, receipt_ids=receipts)
    return packet, children
