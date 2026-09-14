"""Lossless cache codec for deterministic target exploration, never verdicts.

Only explicit dataclass types can be encoded. Decoding permits the result types
alone, not arbitrary imports or executable pickle payloads. Unknown environments
bypass reuse. Complete program/data/environment inputs participate in identity.
"""
from dataclasses import fields, is_dataclass
from functools import lru_cache
import hashlib
import inspect
import json
from pathlib import Path
import zlib

MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_TRACE_ENTRIES = 25000


@lru_cache(maxsize=1)
def classes():
    from solver import mips_differential as d, cfg, callee_execution, linked_callee, callback_abi
    results = [d.TestCase, d.CallEvent, d.WriteEvent, d.InstructionEvent,
               d.RunResult, d.CoverageReport, d.CoverageExploration, d.SemanticStressPanel]
    inputs = [d.Program, cfg.Instruction, callee_execution.Environment,
              callee_execution.Leaf, callee_execution.OutputBuffer,
              linked_callee.LinkedCallee, callback_abi.CallbackProgram]
    return {c.__module__+'.'+c.__name__:c for c in results}, set(results+inputs)


def encode(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    if type(value) is bytes:
        return ['bytes', value.hex()]
    if type(value) in (tuple, list):
        return ['tuple' if type(value) is tuple else 'list', [encode(v) for v in value]]
    if type(value) is dict:
        pairs = [[encode(k), encode(v)] for k,v in value.items()]
        # Dict insertion order is observable Python state, including in
        # program data/environment inputs. Preserve it in keys and results.
        return ['dict', pairs]
    if is_dataclass(value) and type(value) in classes()[1]:
        return ['record', type(value).__module__+'.'+type(value).__name__,
                {f.name:encode(getattr(value, f.name)) for f in fields(value)}]
    raise TypeError('unsupported target cache input: '+type(value).__name__)


def decode(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    kind = value[0]
    if kind == 'bytes':
        return bytes.fromhex(value[1])
    if kind in ('list', 'tuple'):
        result = [decode(v) for v in value[1]]
        return tuple(result) if kind == 'tuple' else result
    if kind == 'dict':
        return {decode(k):decode(v) for k,v in value[1]}
    if kind == 'record':
        cls = classes()[0].get(value[1])
        if cls is None or set(value[2]) != {f.name for f in fields(cls)}:
            raise ValueError('invalid target cache result type or fields')
        return cls(**{k:decode(v) for k,v in value[2].items()})
    raise ValueError('invalid target cache value')


def identity(function, args, kwargs):
    bound = inspect.signature(function).bind(*args, **kwargs)
    bound.apply_defaults()
    return [2, function.__name__, encode(dict(bound.arguments))]


def save(directory, value):
    # A cache is optional work: do not multiply memory for huge exploration
    # traces. The original result still proceeds through ordinary validation.
    if sum(len(run.trace)+len(run.writes)+len(run.calls)+len(run.persistent_state)
           for run in getattr(value, 'runs', ())) > MAX_TRACE_ENTRIES:
        raise TypeError('target result exceeds cache trace admission limit')
    payload = json.dumps(encode(value), sort_keys=True, separators=(',', ':')).encode()
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise TypeError('target result exceeds cache payload admission limit')
    digest = hashlib.sha256(payload).digest()
    temporary = directory/'target.tmp'
    temporary.write_bytes(digest+zlib.compress(payload, 1))
    temporary.replace(directory/'target.bin')


def read(directory):
    path = directory/'target.bin'
    if not path.exists():
        return None
    record = path.read_bytes()
    try:
        inflater = zlib.decompressobj()
        payload = inflater.decompress(record[32:], MAX_PAYLOAD_BYTES+1)
        if len(payload) > MAX_PAYLOAD_BYTES or not inflater.eof or inflater.unused_data:
            raise ValueError('target cache payload exceeds limit or is incomplete')
        if hashlib.sha256(payload).digest() != record[:32]:
            raise ValueError('target cache checksum mismatch')
        return decode(json.loads(payload))
    except (zlib.error, IndexError, KeyError, TypeError) as exc:
        raise ValueError('invalid target cache record') from exc
