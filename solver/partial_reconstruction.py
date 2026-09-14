"""Explicitly incomplete C candidates, isolated from the ordinary repair frontier.

Block ownership is a reconstruction hypothesis, never an equivalence certificate.
Even an empty hole ledger requires the caller's normal full semantic/exact gates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from solver import cfg, code_shapes

MARKER = '__gd_unfinished'
DECLARATION = 'extern void __gd_unfinished(unsigned int);\n'
_CALL = re.compile(r'\b__gd_unfinished\s*\(\s*(0|[1-9][0-9]*)\s*\)\s*;')


class InvalidReconstruction(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise InvalidReconstruction(message)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _seal(value):
    result = copy.deepcopy(value)
    result.pop('manifest_sha256', None)
    result['manifest_sha256'] = _sha(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return result


def marker(hole_id):
    _require(type(hole_id) is int and 0 <= hole_id <= 0xffffffff, 'invalid hole id')
    return f'{MARKER}({hole_id});'


def _calls(text):
    masked = code_shapes._mask(text)
    matches = list(_CALL.finditer(masked))
    remainder = _CALL.sub('', masked)
    _require(MARKER not in remainder, 'marker may only occur as a literal standalone call')
    ids = [int(m[1]) for m in matches]
    _require(len(ids) == len(set(ids)), 'each hole must have exactly one marker')
    return matches


def _signature(source, function):
    region = code_shapes._body(source, function)
    _require(region is not None, 'unsupported or missing function body')
    masked, begin, end = region
    # The existing lexer masks comments/strings and balances delimiters. Be
    # conservative about complex declarators rather than guessing an ABI.
    before = masked[:begin - 1]
    matches = list(re.finditer(r'\b' + re.escape(function) + r'\s*\(', before))
    _require(bool(matches), 'missing signature')
    name = matches[-1].start()
    boundary = max(before.rfind(';', 0, name), before.rfind('}', 0, name), before.rfind('{', 0, name)) + 1
    prefix = before[boundary:name]
    prefix = re.sub(r'(?m)^\s*#[^\n]*', '', prefix).strip()
    prefix = re.sub(r'\b(?:static|extern|inline|__inline__)\b', '', prefix).strip()
    _require(bool(prefix) and re.fullmatch(r'[\w\s*]+', prefix) is not None, 'unsupported return declarator')
    scalar = {'void', 'char', 'short', 'int', 'long', 'signed', 'unsigned', 'float', 'double',
              's8', 's16', 's32', 's64', 'u8', 'u16', 'u32', 'u64', 'f32', 'f64',
              'size_t', 'intptr_t', 'uintptr_t', 'bool', '_Bool', 'const', 'volatile'}
    pointer = '*' in prefix
    _require(pointer or set(prefix.split()) <= scalar, 'unknown or aggregate return type')
    signature = source[boundary:begin - 1]
    _require('...' not in signature, 'variadic signatures are unsupported')
    return begin, end, signature, ('return;' if prefix == 'void' else 'return 0;')


def create(source, function, target_assembly):
    """Keep the exact original signature/context; replace only its body.

    Supply the SAME normalized target assembly used by differential execution,
    so target trace indices refer to this manifest's instruction numbering.
    """
    _require(MARKER not in source, 'reserved marker already present')
    begin, end, signature, fallback = _signature(source, function)
    graph = cfg.build(target_assembly)
    blocks = [{'id': b.id, 'instructions': [i.index for i in b.instructions]}
              for b in graph.blocks.values()]
    _require(bool(blocks), 'empty target CFG')
    current = DECLARATION + source[:begin] + '\n' + marker(0) + '\n' + fallback + '\n' + source[end:]
    return _seal({'version': 1, 'function': function, 'original_source': source,
                  'original_sha256': _sha(source), 'target_assembly': target_assembly,
                  'target_sha256': _sha(target_assembly), 'signature': signature,
                  'source': current, 'source_sha256': _sha(current), 'blocks': blocks,
                  'holes': [{'id': 0, 'blocks': sorted(b['id'] for b in blocks)}],
                  'implemented_blocks': [], 'max_hole_id': 0, 'revision': 0, 'parent_manifest_sha256': None,
                  'completion_requires_full_gates': True})


def validate(manifest):
    _require(manifest.get('version') == 1, 'unsupported manifest version')
    _require(_seal(manifest)['manifest_sha256'] == manifest.get('manifest_sha256'), 'manifest hash mismatch')
    for field in ('source', 'original_source', 'target_assembly'):
        hash_field = {'original_source': 'original_sha256', 'target_assembly': 'target_sha256'}.get(field, field + '_sha256')
        _require(_sha(manifest[field]) == manifest[hash_field], field + ' hash mismatch')
    _require(manifest['source'].startswith(DECLARATION), 'marker declaration changed')
    expected = cfg.build(manifest['target_assembly'])
    _require(manifest['blocks'] == [{'id': b.id, 'instructions': [i.index for i in b.instructions]}
                                  for b in expected.blocks.values()], 'target block map changed')
    original_begin, original_end, signature, _ = _signature(manifest['original_source'], manifest['function'])
    source = manifest['source'][len(DECLARATION):]
    begin, end, current_signature, _ = _signature(source, manifest['function'])
    _require(signature == current_signature == manifest['signature'], 'signature changed')
    _require(source[:begin] == manifest['original_source'][:original_begin] and
             source[end:] == manifest['original_source'][original_end:], 'outside-function source changed')
    ids = [h['id'] for h in manifest['holes']]
    _require(len(ids) == len(set(ids)), 'duplicate hole ids')
    _require(sorted(int(m[1]) for m in _calls(source)) == sorted(ids), 'hole markers disagree with ledger')
    owned = list(manifest['implemented_blocks'])
    for hole in manifest['holes']:
        marker(hole['id'])
        _require(hole['id'] <= manifest['max_hole_id'], 'invalid hole high water mark')
        _require(bool(hole['blocks']), 'empty hole')
        owned.extend(hole['blocks'])
    _require(all(type(b) is int for b in owned), 'block ids must be integers')
    _require(len(owned) == len(set(owned)) and set(owned) == set(expected.blocks), 'blocks must be an exact partition')
    return manifest


def apply(manifest, proposal):
    """Replace one marker statement; all other source bytes remain immutable."""
    validate(manifest)
    _require(type(proposal) is dict, 'proposal must be an object')
    _require(set(proposal) <= {'manifest_sha256', 'hole_id', 'replacement',
                              'implemented_blocks', 'child_holes', 'hypothesis'}, 'unknown proposal field')
    _require(type(proposal.get('manifest_sha256')) is str, 'manifest hash must be a string')
    _require(proposal.get('manifest_sha256') == manifest['manifest_sha256'], 'stale proposal')
    _require(type(proposal.get('hole_id')) is int, 'hole id must be an integer')
    _require(type(proposal.get('replacement')) is str and len(proposal['replacement']) <= 12000,
             'replacement must be a string of at most 12000 characters')
    _require(type(proposal.get('implemented_blocks', [])) is list and
             all(type(b) is int for b in proposal.get('implemented_blocks', [])),
             'implemented blocks must be an integer list')
    _require(type(proposal.get('child_holes', [])) is list and len(proposal.get('child_holes', [])) <= 16,
             'child holes must be a list of at most 16 entries')
    if 'hypothesis' in proposal:
        _require(type(proposal['hypothesis']) is str and len(proposal['hypothesis']) <= 600,
                 'hypothesis must be a string of at most 600 characters')
    for child in proposal.get('child_holes', []):
        _require(type(child) is dict and set(child) == {'id', 'blocks'}, 'invalid child hole fields')
        _require(type(child['id']) is int, 'child id must be an integer')
        _require(type(child['blocks']) is list and bool(child['blocks']) and
                 all(type(b) is int for b in child['blocks']), 'child blocks must be a nonempty integer list')
    hole_id = proposal['hole_id']
    matches = [h for h in manifest['holes'] if h['id'] == hole_id]
    _require(len(matches) == 1, 'unknown hole')
    hole = matches[0]
    replacement = proposal['replacement']
    masked = code_shapes._mask(replacement)
    _require(not re.search(r'#|\\|\?\?|\b(?:asm|__asm|__asm__|goto|longjmp|setjmp)\b', masked), 'unsafe replacement construct')
    _require(code_shapes._close('{' + masked + '}', 0, '{', '}') == len(masked) + 1,
             'replacement escapes its region')
    children = copy.deepcopy(proposal.get('child_holes', []))
    implemented = list(proposal.get('implemented_blocks', []))
    assigned = implemented + [b for h in children for b in h['blocks']]
    _require(len(assigned) == len(set(assigned)) and set(assigned) == set(hole['blocks']), 'proposal must partition all selected blocks')
    used = {h['id'] for h in manifest['holes']}
    child_ids = [h['id'] for h in children]
    for child_id in child_ids:
        marker(child_id)
    _require(all(child_id > manifest['max_hole_id'] for child_id in child_ids), 'child ids must be new')
    _require(len(child_ids) == len(set(child_ids)) and not (used & set(child_ids)), 'child ids must be fresh')
    _require(sorted(int(m[1]) for m in _calls(replacement)) == sorted(child_ids), 'child marker mismatch')
    source = manifest['source']
    calls = _calls(source[len(DECLARATION):])
    match = next(m for m in calls if int(m[1]) == hole_id)
    start, stop = match.start() + len(DECLARATION), match.end() + len(DECLARATION)
    result = copy.deepcopy(manifest)
    result.update(source=source[:start] + replacement + source[stop:],
                  holes=[h for h in manifest['holes'] if h['id'] != hole_id] + children,
                  implemented_blocks=sorted(manifest['implemented_blocks'] + implemented),
                  max_hole_id=max([manifest['max_hole_id']] + child_ids),
                  revision=manifest['revision'] + 1, parent_manifest_sha256=manifest['manifest_sha256'])
    result['source_sha256'] = _sha(result['source'])
    return validate(_seal(result))


def source_for_compile(manifest):
    return validate(manifest)['source']


def classify_row(manifest, row, target_trace_indices, candidate_marker_ids, *, trace_complete=True):
    """Preserve raw comparison; suppress credit for any unfinished execution.

    Markers poison the whole row including a later shared join. Target ownership
    also detects candidate predicates that incorrectly bypass the marker.
    No ownership declaration alone earns correctness credit.
    """
    validate(manifest)
    pending_blocks = {b for h in manifest['holes'] for b in h['blocks']}
    pending_indices = {i for b in manifest['blocks'] if b['id'] in pending_blocks for i in b['instructions']}
    known_indices = {i for b in manifest['blocks'] for i in b['instructions']}
    trace = list(target_trace_indices)
    reasons = []
    if list(candidate_marker_ids):
        reasons.append('candidate_entered_hole')
    if pending_indices.intersection(trace):
        reasons.append('target_entered_pending_block')
    if not trace_complete or not trace or not set(trace) <= known_indices:
        reasons.append('incomplete_execution_trace')
    return {'status': 'unfinished' if reasons else 'observed', 'unfinished_reasons': reasons,
            'raw': copy.deepcopy(row), 'eligible_for_behavior_credit': not reasons,
            'completion_requires_full_gates': True}
