"""Expose the current experiment choices before large source/assembly context."""
import copy
import json

from solver import modelrepair

EXAMPLES = {
    'inspect_compiler': {'hypothesis': 'distinguish pre-as1 allocation from final scheduling'},
    'execution_case': {'hypothesis': 'exercise a boundary input', 'inputs': {'entry_registers': [['a0', 0]]}},
    'inspect_evidence': {'query': 'function_symbol'},
    'replace_source': {'hypothesis': 'a different source structure addresses the residual', 'source': 'complete candidate C'},
    'patch': {'kind': 'expression', 'hypothesis': 'one testable source change', 'edits': [{'old': 'unique current C', 'new': 'replacement C'}]},
    'compiler_probe': {'source': 'self-contained C', 'hypothesis': 'predicted compiler observation'},
    'inspect_definition': {'query': 'TypeOrSymbol'},
    'read_header': {'path': 'include/file.h', 'start': 1, 'end': 80},
    'inspect_diff': {'view': 'first'},
    'inspect_history': {},
    'select_candidate': {'candidate_id': 'c0'},
    'inspect_capabilities': {'query': 'parser'},
    'request_capability': {'hypothesis': 'a shared missing tool behavior', 'task': {'issue_key': 'known shared issue',
        'modules': ['solver/module.py'], 'reproduce': ['tests/test_x.py::test_reproduction'],
        'regression': ['tests/test_x.py'], 'transfer': ['tests/test_y.py::test_transfer']}},
    'record_hypothesis': {'subject': 'global:0xADDRESS', 'alternatives': ['cause A', 'cause B'], 'support': ['receipt id']},
    'finish': {'reason': 'what was established and the concrete remaining obstacle'},
}


def choices(events, active_id, handlers, compiles_left):
    available = {'inspect_definition', 'read_header', 'inspect_diff', 'inspect_history',
                 'select_candidate', 'patch', 'replace_source', 'finish'} | set(handlers or {})
    for name in ('inspect_definition', 'read_header', 'inspect_diff', 'inspect_history',
                 'inspect_evidence', 'inspect_compiler', 'inspect_capabilities'):
        if sum(e.get('action') == name and e.get('status') == 'duplicate-tool'
               and e.get('active_candidate') == active_id for e in events) >= 2:
            available.discard(name)
    if compiles_left < 2:
        available.discard('inspect_compiler')
    if compiles_left < 1:
        available -= {'patch', 'replace_source', 'compiler_probe'}
    return [name for name in EXAMPLES if name in available]


def expose(prompt, available, calls_left, compiles_left, function='function_symbol'):
    start = prompt.index('Return exactly ONE JSON action')
    end = prompt.index('\nRules:', start)
    menu = ('Return exactly ONE JSON action. Choose an experiment that distinguishes causes.\n'
            f'Budget remaining: {calls_left} model turns, {compiles_left} compile units.\n'
            'Compiler phase output and target execution answer factual questions. After inspection, '
            'construct a candidate that tests your hypothesis. Do not repeat failed lookups.\n'
            'AVAILABLE ACTIONS THIS TURN (other actions are disabled):\n')
    menu += '\n'.join(json.dumps({'action': name, **EXAMPLES[name],
                                 **({'query': function} if name == 'inspect_evidence' else {})})
                       for name in available)
    return prompt[:start] + menu + '\n' + prompt[end:]


def schema(available):
    properties = {name: {'type': 'string', 'minLength': 1} for name in
                  ('query', 'path', 'view', 'candidate_id', 'reason', 'source', 'hypothesis', 'subject', 'kind')}
    properties.update(
        start={'type': 'integer'}, end={'type': 'integer'},
        edits=copy.deepcopy(modelrepair.EDIT_SCHEMA['properties']['edits']),
        inputs={'type': 'object'}, task={'type': 'object'},
        alternatives={'type': 'array', 'items': {'type': 'string'}},
        support={'type': 'array', 'items': {'type': 'string'}})
    alternatives = []
    for name in available:
        fields = list(EXAMPLES[name])
        alternatives.append({'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': [name]},
            **{key: copy.deepcopy(properties[key]) for key in fields}},
            'required': ['action', *fields], 'additionalProperties': False})
    return {'anyOf': alternatives}


def history_view(events, max_chars):
    """Bound the prompt view; the durable event list is never changed."""
    if max_chars <= 0:
        return '(prior events omitted; use inspect_history for details)'
    lines = []
    for event in reversed(events[-10:]):
        view = {key: event[key] for key in
                ('step', 'action', 'status', 'active_candidate', 'child_candidate',
                 'compiled', 'exact', 'score', 'hypothesis', 'error')
                if key in event}
        residual = event.get('residual') or {}
        if residual:
            view['residual'] = {key: residual[key] for key in
                ('faults', 'instruction_delta', 'positional_byte_distance', 'first_difference',
                 'compiler_error_signature') if key in residual}
        if 'observation' in event:
            view['observation'] = event['observation'][:min(1200, max_chars // 2)]
            if len(event['observation']) > len(view['observation']):
                view['observation_omitted_chars'] = len(event['observation']) - len(view['observation'])
        line = json.dumps(view, sort_keys=True)
        if len(line) + sum(len(part) + 1 for part in lines) > max_chars:
            break
        lines.append(line)
    return '\n'.join(reversed(lines)) or '(prior events omitted; use inspect_history for details)'
