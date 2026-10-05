"""Deliberate layout stress cases, separate from operational random audits."""
from __future__ import annotations
import re

from .manifest import digest


def layout_variants(source: str):
    yield 'leading-blank-lines', '\n\n' + source
    yield 'line-directive-1000', '#line 1000 "candidate.c"\n' + source
    # Tokenize comments/literals first; only punctuation in C code is moved.
    token = re.compile(r'^[ \t]*#[^\n]*(?:\\\n[^\n]*)*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*|;', re.S | re.M)
    split = token.sub(lambda m: ';\n' if m[0] == ';' else m[0], source)
    if split != source:
        yield 'split-statements', split
    # Blank lines preserve macro/directive topology and // termination.
    spaced = '\n'.join(line + ('\n' if line.strip() and not line.rstrip().endswith('\\') else '')
                       for line in source.splitlines()) + '\n'
    if spaced != source:
        yield 'spaced-lines', spaced


def audit(source, variants, compile_candidate, key, same_object, *, budget=16):
    if type(budget) is not int or budget < 1:
        raise ValueError('audit budget must be a positive integer')
    result = {'compiles': 0, 'key_calls': 0, 'same_key_pairs': 0, 'different_key_pairs': 0,
              'missing_key_pairs': 0, 'conclusive': 0, 'violations': 0, 'unavailable': 0,
              'pairs': [], 'scope': 'deliberate stress sample; not a population failure rate'}

    def get_key(text):
        result['key_calls'] += 1
        try:
            return key(text)
        except (OSError, ValueError, RuntimeError):
            return None

    prior = compile_candidate(source, 'key-baseline', None)
    result['compiles'] += 1
    baseline_key = get_key(source)
    seen = {source}
    for index, (label, candidate) in enumerate(variants):
        if result['compiles'] >= budget or index >= 256:
            break
        if candidate in seen:
            continue
        seen.add(candidate)
        actual = compile_candidate(candidate, label, source)
        result['compiles'] += 1
        candidate_key = get_key(candidate)
        row = {'label': label, 'source_sha256': digest(candidate.encode()),
               'baseline_key': baseline_key, 'candidate_key': candidate_key,
               'raw_object_equal': bool(prior.obj == actual.obj) if prior.obj and actual.obj else None}
        if baseline_key is None or candidate_key is None:
            result['missing_key_pairs'] += 1
            row['status'] = 'missing-key'
        elif baseline_key != candidate_key:
            result['different_key_pairs'] += 1
            row['status'] = 'different-key'
        else:
            result['same_key_pairs'] += 1
            verdict = same_object(prior, actual) if prior.compiled and actual.compiled else None
            row['status'] = 'agree' if verdict is True else 'violation' if verdict is False else 'unavailable'
            result['conclusive'] += verdict is not None
            result['violations'] += verdict is False
            result['unavailable'] += verdict is None
        result['pairs'].append(row)
    return result
