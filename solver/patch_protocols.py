"""Experimental patch protocols; all normal source validators remain mandatory."""
from dataclasses import dataclass
import copy
import re
from solver import edit_slots, modelrepair


def locations(source):
    return {key.split(':', 1)[1]: span for key, span in edit_slots.slots(source).items()}


def patch_schema(allowed):
    schema = copy.deepcopy(modelrepair.EDIT_SCHEMA)
    schema['properties']['edits']['items'] = {
        'type': 'object', 'required': ['slot', 'new'], 'additionalProperties': False,
        'properties': {'slot': {'type': 'string', 'enum': list(allowed)},
                       'new': {'type': 'string'}}}
    return schema


def selection_schema(allowed):
    return {'type': 'object', 'required': ['slots', 'reason'], 'additionalProperties': False,
            'properties': {'slots': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                                    'uniqueItems': True,
                                    'items': {'type': 'string', 'enum': list(allowed)}},
                           'reason': {'type': 'string', 'minLength': 1, 'maxLength': 400}}}


@dataclass(frozen=True)
class Selection:
    source_sha256: str
    slots: tuple[str, ...]


def select(source, response):
    value = next(modelrepair._objects(response), None)
    if not isinstance(value, dict) or set(value) != {'slots', 'reason'}:
        raise ValueError('location selection must contain slots and reason only')
    slots = value['slots']
    if (not isinstance(slots, list) or not 1 <= len(slots) <= 4 or
            any(not isinstance(s, str) or s not in locations(source) for s in slots) or
            len(set(slots)) != len(slots) or not isinstance(value['reason'], str) or
            not 1 <= len(value['reason'].strip()) <= 400):
        raise ValueError('invalid location selection')
    return Selection(modelrepair._digest(source), tuple(slots))


def apply(source, response, *, allowed=None, selection=None):
    if selection is not None:
        if selection.source_sha256 != modelrepair._digest(source):
            raise ValueError('stale location selection')
        allowed = selection.slots
    allowed = set(locations(source) if allowed is None else allowed)
    value = next(modelrepair._objects(response), None)
    if not isinstance(value, dict) or not isinstance(value.get('edits'), list):
        raise ValueError('missing patch edits')
    for edit in value['edits']:
        if (not isinstance(edit, dict) or set(edit) != {'slot', 'new'} or
                not isinstance(edit['slot'], str) or edit['slot'] not in allowed):
            raise ValueError('patch must use only allowed slot/new pairs')
    proposal = modelrepair.parse_proposal(response, source=source, truncate_hypothesis=True)
    return modelrepair.apply_proposal(source, proposal)


POLICY = '''Return one JSON object with kind, hypothesis, and 1-4 edits.
Each edit MUST be {"slot":"a listed location","new":"replacement C"}.
No old/new addressing, binary patches, paths, offsets, includes, pragmas or assembly.
Line slots replace the COMPLETE line. DECLARATIONS inserts file-scope declarations,
not local variables. Insert a local by replacing the function-opening line with
that line plus the new local. No no-ops, guessed layouts or public ABI changes.
The JSON schema enumerates the only allowed locations. Unverified earlier output
is not evidence. Only use types/layouts supported by the supplied information.
'''


def slot_prompt(original):
    marker = 'TARGET ASSEMBLY (READ-ONLY):'
    if marker not in original:
        raise ValueError('expected saved narrow repair prompt')
    context = original[original.index(marker):]
    context = context.replace('Copy each old span exactly from CURRENT C.',
                              'Choose a slot from CURRENT C.')
    return POLICY + '\n' + context


def focused(source, rejected, error):
    """Show source-bound nearby lines, not the entire assembly/evidence packet."""
    available = locations(source)
    identifiers = set(re.findall(r'\b[A-Za-z_]\w*\b', rejected)) - {
        'old', 'new', 'kind', 'hypothesis', 'edits', 'slot', 'void', 'int',
        'struct', 'return', 'if', 'else', 'extern', 's32', 's16', 'u16', 'u8'}
    lines = source.splitlines()
    hits = [i for i, line in enumerate(lines) if identifiers.intersection(re.findall(r'\b\w+\b', line))]
    selected = {j for i in hits[:12] for j in range(max(0, i-1), min(len(lines), i+2))}
    selected.update(i for i, line in enumerate(lines) if re.search(r'\)\s*\{', line))
    allowed = ['DECLARATIONS'] + [s for s in available if s.startswith('L') and int(s[1:])-1 in selected]
    snippets = '\n'.join(f'{s}: {source[a:b]}' for s, (a,b) in available.items() if s in allowed)
    prompt = (POLICY + '\nRepair this rejected edit using the supplied source lines. '
              'If it proposes machine bytes, reconstruct a supported C edit instead; never apply binary bytes.\n'
              'VALIDATOR ERROR:\n' + error + '\nREJECTED OUTPUT (NOT APPLIED, NOT EVIDENCE):\n' + rejected +
              '\nCURRENT SOURCE LOCATIONS:\n' + snippets +
              '\nDECLARATIONS is file-scope insertion after includes; add a trailing newline.\n')
    return prompt, allowed
