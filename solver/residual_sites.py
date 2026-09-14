"""Direct compiler attribution, with source hypotheses/interventions for gaps.

Object diffs join by candidate listing position to verified original-object line
records. Syntax associations are fallback hypotheses; an edit's compiler response
establishes influence, not a unique instruction owner. Source hashes prevent
applying old line numbers to new C. No reference C or debug rebuild is needed.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import re
import json

from solver import code_shapes


def digest(text: str) -> str:
    return sha256(text.encode('utf-8')).hexdigest()


def mismatches(diff: str) -> list[dict]:
    rows = []
    occurrences = Counter()
    candidate_line = None
    for line in diff.splitlines():
        hunk = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
        if hunk:
            candidate_line = int(hunk[1])
            continue
        position = candidate_line if line.startswith('+') else None
        if candidate_line is not None and line[:1] in (' ', '+') and not line.startswith('+++'):
            candidate_line += 1
        if line.startswith(('---', '+++')) or line[:1] not in ('-', '+'):
            continue
        instruction = line[1:].strip()
        m = re.match(r'([a-z][a-z0-9.]*)\b', instruction)
        if not m:
            continue
        op = m[1]
        if op in {'lw', 'lh', 'lhu', 'lb', 'lbu', 'lwc1', 'ldc1',
                  'sw', 'sh', 'sb', 'swc1', 'sdc1', 'lwl', 'lwr', 'swl', 'swr'}:
            family = 'memory'
        elif op.startswith('b') or op in {'j', 'jr', 'jal', 'jalr', 'slt', 'slti', 'sltu', 'sltiu'}:
            family = 'control'
        else:
            family = 'expression'
        text = line[0] + re.sub(r'\s+', ' ', instruction)
        occurrence = occurrences[text]
        occurrences[text] += 1
        rows.append({'id': digest(f'{text}:{occurrence}')[:16],
                     'side': 'candidate' if line[0] == '+' else 'target',
                     'instruction': instruction, 'family': family,
                     'candidate_listing_line': position})
    return rows


def edit_region(before: str, after: str) -> dict:
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    stop, child_stop = len(before), len(after)
    while stop > start and child_stop > start and before[stop - 1] == after[child_stop - 1]:
        stop -= 1
        child_stop -= 1
    return {'start': start, 'stop': stop,
            'start_line': before.count('\n', 0, start) + 1,
            'end_line': before.count('\n', 0, max(start, stop - 1)) + 1,
            'excerpt': before[start:stop][:600]}


def source_map(source: str, function: str, diff: str, direct: dict | None = None) -> dict:
    residuals = mismatches(diff)
    region = code_shapes._body(source, function)
    sites = []
    directly_mapped = set()
    gaps = []
    direct_valid = bool(direct and direct.get('status') == 'verified' and
                        direct.get('source_sha256') == digest(source) and
                        direct.get('diff_sha256') == digest(diff))
    direct_rows = {r['normalized_line']: r for r in direct['instructions']} if direct_valid else {}
    source_lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in source_lines:
        offsets.append(offsets[-1] + len(line))
    for residual in residuals:
        row = direct_rows.get(residual['candidate_listing_line'])
        number = row.get('candidate_line') if row else None
        if (region and number and 1 <= number <= len(source_lines) and
                offsets[number] > region[1] - 1 and offsets[number - 1] < region[2] + 1):
            # IDO attributes epilogue restores to the closing brace. That is
            # a direct function location, even though no C statement lives there.
            start, stop = max(offsets[number - 1], region[1] - 1), min(offsets[number], region[2] + 1)
            sites.append({'start': start, 'stop': stop, 'start_line': number,
                          'end_line': number, 'families': [residual['family']],
                          'mismatch_ids': [residual['id']], 'evidence': 'direct-compiler-line',
                          'section': row['section'], 'address': row['address'], 'bytes': row['bytes'],
                          'excerpt': source[start:stop].strip()[:300]})
            directly_mapped.add(residual['id'])
        else:
            reason = ('target-only instruction; no candidate instruction to attribute' if residual['side'] == 'target' else
                      'no unified-diff candidate position' if residual['candidate_listing_line'] is None else
                      'source-bound object line map unavailable' if not direct_valid else
                      'compiler line absent, external, or outside candidate function')
            gaps.append({'mismatch_id': residual['id'], 'reason': reason})
    if region is not None:
        masked, begin, end = region
        offset = 0
        for number, line in enumerate(masked.splitlines(keepends=True), 1):
            start, stop = max(begin, offset), min(end, offset + len(line))
            offset += len(line)
            if stop <= start:
                continue
            text = masked[start:stop]
            families = set()
            if re.search(r'\b(if|else|while|for|do|switch|return)\b|[?:<>]|==|!=', text):
                families.add('control')
            if re.search(r'\[|->|\*', text):
                families.add('memory')
            if re.search(r'[=+*/%&|^~-]|\breturn\b', text):
                families.add('expression')
            implicated = [row['id'] for row in residuals if row['family'] in families
                          and row['id'] not in directly_mapped]
            if implicated:
                sites.append({'start': start, 'stop': stop, 'start_line': number,
                              'end_line': number, 'families': sorted(families),
                              'mismatch_ids': implicated, 'evidence': 'syntax-hypothesis',
                              'excerpt': source[start:stop].strip()[:300]})
    return {'source_sha256': digest(source), 'diff_sha256': digest(diff),
            'mapping_kind': 'direct compiler lines first; syntax/intervention fallback for gaps',
            'instruction_ownership_proven': False,
            'directly_mapped_mismatches': len(directly_mapped), 'gaps': gaps,
            'direct_attribution_status': direct.get('status') if direct_valid else 'unavailable-or-stale',
            'mismatches': residuals, 'sites': sites}


def _overlap(a: dict, b: dict) -> bool:
    return a['start'] < max(b['stop'], b['start'] + 1) and b['start'] < max(a['stop'], a['start'] + 1)


def rank(source: str, variant, mapping: dict, history=()) -> tuple:
    """Evidence first, opcode/source-family affinity second; no admission gate."""
    region = edit_region(source, variant.source)
    affinity = len({mid for site in mapping['sites'] if _overlap(region, site)
                    for mid in site['mismatch_ids']})
    direct_hits = len({mid for site in mapping['sites'] if _overlap(region, site)
                       and site['evidence'] == 'direct-compiler-line' for mid in site['mismatch_ids']})
    supported = inert = 0
    for row in history:
        probe = row.get('source_intervention', {})
        if (probe.get('parent_source_sha256') != mapping['source_sha256'] or
                probe.get('parent_diff_sha256') != mapping['diff_sha256']):
            continue
        if not probe.get('semantic_clean') or not _overlap(region, probe['region']):
            continue
        # Do not let a neutral spelling suppress an entire source region.
        same_family = probe['family'] == variant.label.split('@', 1)[0]
        supported += int(probe['removed_count'] > probe['added_count'])
        inert += int(same_family and probe['outcome'] == 'residual-unchanged')
    return (-direct_hits, -supported, inert, -affinity)


def intervention(before: str, after: str, before_diff: str, after_diff: str,
                 label: str, *, compiled: bool, semantic_clean: bool,
                 exact: bool) -> dict:
    old = {r['id'] for r in mismatches(before_diff)}
    new = {r['id'] for r in mismatches(after_diff)} if compiled else old
    removed, added = sorted(old - new), sorted(new - old)
    return {'parent_source_sha256': digest(before), 'child_source_sha256': digest(after),
            'parent_diff_sha256': digest(before_diff), 'child_diff_sha256': digest(after_diff),
            'region': edit_region(before, after), 'family': label.split('@', 1)[0],
            'compiled': compiled, 'semantic_clean': semantic_clean, 'exact': exact,
            'removed_mismatch_ids': removed, 'added_mismatch_ids': added,
            'removed_count': len(removed), 'added_count': len(added),
            'outcome': ('compile-failed' if not compiled else
                        'semantic-regression' if not semantic_clean else
                        'byte-exact' if exact else
                        'residual-changed' if removed or added else 'residual-unchanged'),
            'evidence': 'compiler-intervention; influence may extend beyond the edited lines'}


def render(source: str, function: str, diff: str, history=(), direct: dict | None = None) -> str:
    mapping = source_map(source, function, diff, direct)
    lines = ['MISMATCH-GUIDED SOURCE ALTERNATIVES (OSS fallback after bounded deterministic search)',
             'DIRECT entries are original-object compiler line records; LIKELY entries are fallback hypotheses.',
             'Compiler attribution is direct; it does not imply the source line is the sole causal owner.',
             'Propose one bounded equivalent implementation at an implicated region. Preserve the',
             'observed semantic contract; use compiler experiments to justify expanding to dependent code.']
    for row in mapping['mismatches'][:16]:
        lines.append(f"- mismatch {row['id']} {row['side']}: {row['instruction']}")
    for site in sorted(mapping['sites'], key=lambda s: (s['evidence'] != 'direct-compiler-line', -len(s['mismatch_ids'])))[:12]:
        label = (f"DIRECT {site['section']}+0x{site['address']:x} [{site['bytes']}]" if site['evidence'] == 'direct-compiler-line' else 'LIKELY')
        lines.append(f"- {label} L{site['start_line']} ({','.join(site['families'])}): {site['excerpt']}")
    lines.append(f"Directly attributed mismatches: {mapping['directly_mapped_mismatches']}; gaps: {len(mapping['gaps'])}.")
    probes = [r['source_intervention'] for r in history
              if r.get('source_intervention', {}).get('parent_source_sha256') == mapping['source_sha256']
              and r['source_intervention'].get('parent_diff_sha256') == mapping['diff_sha256']]
    for probe in probes[-12:]:
        region = probe['region']
        lines.append(f"- tested L{region['start_line']}-{region['end_line']} {probe['family']}: "
                     f"{probe['outcome']}; {probe['removed_count']} residual rows removed, "
                     f"{probe['added_count']} added; semantic_clean={probe['semantic_clean']}")
        if probe['removed_mismatch_ids']:
            lines.append('  affected prior mismatch IDs: ' + ', '.join(probe['removed_mismatch_ids'][:8]))
    if not mapping['sites']:
        lines.append('- No localized source association; allow a broader bounded source-shape proposal.')
    lines.append('Try an untested spelling; do not repeat inert or semantically rejected experiments.')
    return '\n'.join(lines)


def record(conn, receipt_id: int | None, probe: dict) -> None:
    if receipt_id is None:
        return
    conn.execute('CREATE TABLE IF NOT EXISTS source_interventions ('
                 'child_attempt_id INTEGER PRIMARY KEY, parent_source_sha256 TEXT NOT NULL, '
                 'parent_diff_sha256 TEXT NOT NULL, payload TEXT NOT NULL)')
    conn.execute('CREATE INDEX IF NOT EXISTS source_interventions_parent ON '
                 'source_interventions(parent_source_sha256, parent_diff_sha256)')
    conn.execute('INSERT OR REPLACE INTO source_interventions VALUES (?, ?, ?, ?)',
                 (receipt_id, probe['parent_source_sha256'], probe['parent_diff_sha256'],
                  json.dumps(probe, sort_keys=True)))
    conn.commit()


def load(conn, source: str, diff: str, limit: int = 128) -> list[dict]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='source_interventions'").fetchone():
        return []
    rows = conn.execute('SELECT payload FROM source_interventions WHERE '
                        'parent_source_sha256=? AND parent_diff_sha256=? '
                        'ORDER BY child_attempt_id DESC LIMIT ?',
                        (digest(source), digest(diff), max(0, limit))).fetchall()
    return [{'source_intervention': json.loads(row[0])} for row in reversed(rows)]
