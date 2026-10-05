"""Bounded model-proposed inputs checked against the target before reuse.

The tool supplies no expected output. Target and candidate execution use the
same case and the semantic lane's admitted ABI and callee environment. Results
are observations on finite synthetic states, never a semantic certificate.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from solver import mips_differential as differential, workspace


CASE_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'entry_registers': {'type': 'array', 'maxItems': 4,
                            'items': {'type': 'array', 'prefixItems': [
                                {'enum': ['a0', 'a1', 'a2', 'a3']},
                                {'type': 'integer', 'minimum': 0, 'maximum': 0xffffffff}],
                                'minItems': 2, 'maxItems': 2}},
        'player_writes': {'type': 'array', 'maxItems': 4,
                          'items': {'type': 'array', 'minItems': 3, 'maxItems': 3}},
        'global_writes': {'type': 'array', 'maxItems': 4,
                          'items': {'type': 'array', 'minItems': 3, 'maxItems': 3}},
    },
}
CASE_BRIEF = ("execution_case payload: {entry_registers:[[a0|a1|a2|a3,uint32],...], "
              "player_writes:[[offset,width,value],...], "
              "global_writes:[]}. Global writes currently decline because no binary-proven "
              "writable-RAM extents are admitted. "
              "Widths are 1, 2, or 4; at most four entries per list and eight writes total. "
              "Omit empty lists. Do not supply expected outputs, code, addresses, or call results.")
_SCOPE = ('finite synthetic target-completed cases; game-reachable input domain unproven; '
          'not universal semantic proof')
_SYNTHETIC_DEBT = ('entry registers, pointer values and seeded memory are synthetic; '
                   'target completion does not prove game-reachable input or valid ABI pointer')


def _integer(value, maximum, label):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        raise ValueError(f'{label} must be an unsigned integer <= {maximum}')
    return value


def _validate_keys(payload):
    if not isinstance(payload, dict):
        raise ValueError('execution case payload must be an object')
    unknown = set(payload) - set(CASE_SCHEMA['properties'])
    if unknown:
        raise ValueError('unsupported case field: ' + ', '.join(sorted(map(str, unknown))))


def _writes(rows, label):
    if not isinstance(rows, list) or len(rows) > 4:
        raise ValueError(f'{label} must contain at most four writes')
    output = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError(f'{label} entry must be [location,width,value]')
        location, width, value = row
        if type(width) is not int or width not in (1, 2, 4):
            raise ValueError('write width must be 1, 2, or 4')
        _integer(value, (1 << (8 * width)) - 1, 'write value')
        _integer(location, 0x2000 - width, 'player offset')
        if location % width:
            raise ValueError('player write must be aligned')
        output.append(tuple(row))
    return tuple(output)


def _run_summary(run):
    """Keep a bounded diagnostic receipt; the comparison uses the full run."""
    if run is None:
        return None
    result = run.to_dict()
    for name, limit in (('calls', 32), ('concrete_calls', 16), ('abi_violations', 32)):
        rows = result[name]
        result[name] = rows[:limit]
        result[name + '_count'] = len(rows)
    result['receipt_limits'] = {'calls': 32, 'concrete_calls': 16,
                                'writes': 40, 'instruction_trace': 200,
                                'abi_violations': 32}
    return result


def _metadata(rows, limit=16):
    return [row if len(json.dumps(row, default=str)) <= 2048 else str(row)[:2048]
            for row in list(rows or ())[:limit]]


class Tools:
    """One investigation's case budget and retained target-completed inputs."""

    def __init__(self, repo, ws, function, output, panel=None):
        self.repo, self.ws = Path(repo), Path(ws)
        self.function, self.output = function, Path(output)
        self.panel = panel
        self._cases: list[differential.TestCase] = []
        self._proposals = 0
        self._replays = 0

    @property
    def cases(self):
        return tuple(asdict(case) for case in self._cases)

    def _panel(self):
        panel = self.panel
        for _ in range(4):
            if panel is None or hasattr(panel, 'target'):
                break
            panel = getattr(panel, 'base', None) or getattr(panel, 'panel', None)
        return panel if panel is not None and hasattr(panel, 'target') else None

    def _case(self, payload, panel):
        _validate_keys(payload)
        registers = payload.get('entry_registers', [])
        if not isinstance(registers, list) or len(registers) > 4:
            raise ValueError('entry_registers must contain at most four arguments')
        args = []
        for row in registers:
            if (not isinstance(row, list) or len(row) != 2 or
                    row[0] not in ('a0', 'a1', 'a2', 'a3')):
                raise ValueError('entry register must be [a0|a1|a2|a3,uint32]')
            args.append((row[0], _integer(row[1], 0xffffffff, 'register value')))
        if len({name for name, _ in args}) != len(args):
            raise ValueError('duplicate entry register')
        player = _writes(payload.get('player_writes', []), 'player_writes')
        global_rows = payload.get('global_writes', [])
        if not isinstance(global_rows, list) or len(global_rows) > 4:
            raise ValueError('global_writes must contain at most four writes')
        if global_rows:
            raise ValueError('global write needs an independently admitted writable RAM extent; none is available')
        globals_ = ()
        if len(player) + len(globals_) > 8:
            raise ValueError('execution case exceeds eight memory writes')
        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        seed = int(hashlib.sha256((str(getattr(panel, 'identity', '')) + canonical).encode()).hexdigest()[:8], 16)
        return differential.TestCase(f'model-case-{self._proposals + 1}', seed,
                                     player_writes=player, global_writes=globals_,
                                     entry_registers=tuple(args))

    def _identity(self, source, obj, panel):
        dump = obj.with_name(obj.stem + '_object_dump_normalized.s') if obj else None
        panel_debt = getattr(panel, 'debt', ()) if panel else ()
        obstructions = getattr(panel, 'execution_obstructions', ()) if panel else ()
        return {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                'object_sha256': hashlib.sha256(obj.read_bytes()).hexdigest() if obj and obj.is_file() else None,
                'candidate_assembly_sha256': hashlib.sha256(dump.read_bytes()).hexdigest()
                    if dump and dump.is_file() else None,
                'target_sha256': hashlib.sha256(panel.target.encode()).hexdigest() if panel else None,
                'panel_sha256': getattr(panel, 'identity', None),
                'scope': _SCOPE,
                'domain_validated': False,
                'authoritative': False,
                'candidate_binding': 'caller-supplied source and object; hashes are identity only',
                'execution_debt': [_SYNTHETIC_DEBT, *_metadata(panel_debt)],
                'panel_debt_count': len(panel_debt),
                'execution_obstructions': _metadata(obstructions),
                'execution_obstruction_count': len(obstructions)}

    def _write_receipt(self, label, record):
        self.output.mkdir(parents=True, exist_ok=True)
        path = self.output / f'{label}.json'
        if path.exists():
            raise RuntimeError('execution receipt already exists')
        record['receipt'] = str(path)
        path.write_text(json.dumps(record, indent=2) + '\n')
        return record

    def _compare(self, panel, case, obj):
        if obj is None or not obj.is_file():
            return None
        dump = obj.with_name(obj.stem + '_object_dump_normalized.s')
        if not dump.is_file():
            return None
        assembly = workspace.semantic_assembly(dump.read_text(), obj)
        row, = differential.run_suite(panel.target, assembly, (case,),
            target_name=self.function, candidate_name=self.function + '-candidate',
            call_arities=panel.arities, return_registers=panel.returns,
            max_steps=min(panel.max_steps, 10000),
            callee_environment=panel.callee_environment)
        from eval.semantic_lane import classify_unknown_direct_arguments
        return classify_unknown_direct_arguments(row, getattr(panel, 'call_contracts', {}))

    def propose(self, payload: dict, candidate_source: str, candidate_object: Path | None):
        """Execute the target first; retain only a completed, ABI-valid input."""
        _validate_keys(payload)
        if self._proposals >= 8:
            raise ValueError('execution-case budget exhausted (8)')
        panel = self._panel()
        if panel is None:
            self._proposals += 1
            return self._write_receipt(f'case-{self._proposals:04d}',
                {'status': 'unavailable', 'reason': 'semantic target panel is unavailable',
                 **self._identity(candidate_source, candidate_object, None)})
        case = self._case(payload, panel)
        self._proposals += 1
        identity = self._identity(candidate_source, candidate_object, panel)
        target = differential.execute_case(differential.Program.parse(self.function, panel.target), case,
            call_arities=panel.arities, return_registers=panel.returns,
            max_steps=min(panel.max_steps, 10000), callee_environment=panel.callee_environment)
        record = {'input': asdict(case), 'target': _run_summary(target), 'candidate': None,
                  'comparison': None, **identity}
        if target.status not in differential.COMPLETED_STATUSES or target.abi_violations:
            record['status'] = 'target_inconclusive'
            record['reason'] = 'target did not complete within admitted execution/ABI bounds'
            return self._write_receipt(f'case-{self._proposals:04d}', record)
        self._cases.append(case)
        row = self._compare(panel, case, candidate_object)
        if row is None:
            record['status'] = 'target_completed_candidate_unavailable'
        else:
            record['target'] = _run_summary(row.target)
            record['candidate'] = _run_summary(row.candidate)
            record['comparison'] = row.status
            record['reasons'] = list(row.reasons)
            record['first_divergence'] = row.first_divergence
            record['status'] = ('observed_failure' if row.status == 'failed' else
                                'observed_pass_with_execution_debt' if row.status == 'passed'
                                else 'inconclusive')
        return self._write_receipt(f'case-{self._proposals:04d}', record)

    def evaluate(self, candidate_source: str, candidate_object: Path | None):
        """Replay every retained input against a later candidate, without promotion."""
        panel = self._panel()
        self._replays += 1
        record = {**self._identity(candidate_source, candidate_object, panel), 'cases': []}
        if panel is None or not self._cases:
            record['status'] = 'no_reusable_cases'
            return self._write_receipt(f'replay-{self._replays:04d}', record)
        for case in self._cases:
            row = self._compare(panel, case, candidate_object)
            record['cases'].append({'input': asdict(case),
                'comparison': row.status if row else 'unavailable',
                'reasons': list(row.reasons) if row else ['candidate object/dump unavailable'],
                'first_divergence': row.first_divergence if row else '',
                'target': _run_summary(row.target) if row else None,
                'candidate': _run_summary(row.candidate) if row else None})
        statuses = {row['comparison'] for row in record['cases']}
        record['status'] = ('observed_failure' if 'failed' in statuses else
                            'inconclusive' if statuses - {'passed'} else
                            'observed_pass_with_execution_debt')
        return self._write_receipt(f'replay-{self._replays:04d}', record)
