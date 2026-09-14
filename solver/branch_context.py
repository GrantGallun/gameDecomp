"""Bounded, observed control histories for differential repair (never verdicts)."""
import hashlib
import json

from solver import mips_differential as differential


def _decisions(run, end=None):
    return [event for event in run.trace[:end]
            if event.effect.startswith(('branch taken;', 'branch not taken;',
                                        'indirect control transfer to '))]


def fingerprint(row):
    """Keep same-reason failures on distinct executed paths separate.

    Register values deliberately do not split otherwise identical paths. The
    two program-local histories are hashed together, never ordinally aligned.
    """
    paths = [[(e.instruction, e.effect) for e in _decisions(run)]
             for run in (row.target, row.candidate)]
    return hashlib.sha256(json.dumps(paths, separators=(',', ':')).encode()).hexdigest()


def failure_key(row):
    return tuple(row.reasons), fingerprint(row)


def _history(run, end, limit):
    events = _decisions(run, end)
    # Preserve the entry guard even when a long loop separates it from a store.
    chosen = events if len(events) <= limit else events[:2] + events[-(limit-2):]
    rows = []
    truncated = 0
    def clip(value):
        nonlocal truncated
        value = str(value)
        truncated += max(0, len(value)-240)
        return value[:240]
    for event in chosen:
        rows.append({'ordinal':event.ordinal, 'instruction':event.instruction,
            'instruction_text':clip(event.text), 'decision':clip(event.effect),
            'operands':[{'register':clip(name), 'value':f'{value:#010x}',
                         'provenance':clip(origin)}
                        for name,value,origin in event.reads[:3]],
            'omitted_operands':max(0,len(event.reads)-3)})
    return {'status':run.status, 'trace_end_exclusive':end,
            'decisions':rows, 'total_decisions':len(events),
            'omitted_decisions':len(events)-len(chosen),
            'omitted_text_characters':truncated}


def packet(row, *, max_decisions=6):
    """Control prefixes ending at each relevant observed mismatch checkpoint."""
    limit = min(6, max(3, max_decisions))
    anchors = []
    if row.target.status == row.candidate.status:
        call = differential._first_call_difference(row)
        if call:
            anchors.append(('call', call[1], call[2]))
    write = differential._first_aligned_write_difference(row)
    if write:
        anchors.append(('persistent_write', write[1], write[2]))
    if (row.target.return_values != row.candidate.return_values or not anchors):
        anchors.append(('return_or_terminal', None, None))
    contexts = []
    for kind,left,right in anchors:
        context = {'observable':kind}
        for side,run,event in (('target',row.target,left),('candidate',row.candidate,right)):
            end = min(len(run.trace), event.trace_position+1) if event else len(run.trace)
            context[side] = _history(run,end,limit)
            context[side]['checkpoint_present'] = event is not None if kind != 'return_or_terminal' else True
        contexts.append(context)
    result = {'version':1, 'path_sha256':fingerprint(row),
        'interpretation':'Independent executed prefixes, not aligned branches or proven control dependencies. '
                         'A resolved indirect jump is not proof of an original C switch. '
                         'Omitted decisions may contain relevant guards.',
        'contexts':contexts}
    # A hard serialized bound applies across anchors, not just per history.
    # Omission counts remain explicit and full execution receipts are untouched.
    histories = [context[side] for context in contexts for side in ('target','candidate')]
    while len(json.dumps(result)) > 12000:
        history = max(histories,key=lambda h:len(json.dumps(h['decisions'])))
        if not history['decisions']:
            break
        index = 1 if len(history['decisions']) > 2 else 0
        history['decisions'].pop(index)
        history['omitted_decisions'] += 1
    return result


def passing_alternative(row, cases, rows):
    """One observed passing contrast with a different TARGET path, if available."""
    target_path = [(e.instruction,e.effect) for e in _decisions(row.target)]
    for case,other in zip(cases,rows):
        if other.status != 'passed':
            continue
        if [(e.instruction,e.effect) for e in _decisions(other.target)] == target_path:
            continue
        from dataclasses import asdict
        return {'input':asdict(case), 'status':other.status,
                'note':'Different observed target path; preserve this passing case. '
                       'This is not a counterfactual with all other inputs held fixed.',
                'path_context':packet(other,max_decisions=3)}
    return None
