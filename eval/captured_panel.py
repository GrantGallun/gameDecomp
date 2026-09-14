"""Add ROM-bound runtime captures to the existing synthetic semantic panel."""
import hashlib
import json
from pathlib import Path
from solver import evidence_schedule, runtime_capture, workspace


class Panel:
    def __init__(self, base, repo, ws, function, captures):
        self.base, self.repo, self.ws, self.function = base, repo, ws, function
        self.captures, self.cache = captures, {}

    @property
    def report(self):
        return {**self.base.report, 'runtime_captures': [c['sha256'] for c in self.captures]}

    def __call__(self, state):
        original = self.base(state)
        if original is None:
            return None
        key = hashlib.sha256(state.source.encode()).hexdigest()
        if key in self.cache:
            return self.cache[key]
        import yaml
        config = yaml.safe_load((self.repo / 'snowboardkids.yaml').read_text())
        rom = self.repo / config['options']['target_path']
        rows = []
        for record in self.captures:
            try:
                if record['plan']['function'] != self.function:
                    raise ValueError('capture function identity mismatch')
                obj = state.object_path
                candidate = workspace.semantic_assembly(obj.with_name(obj.stem + '_object_dump_normalized.s').read_text(), obj)
                target = workspace.semantic_assembly(workspace.target_asm(self.ws, self.function), self.ws / 'target.o')
                report = runtime_capture.replay(record, rom, target,
                                                candidate, repo=self.repo,
                                                return_registers=tuple(record['plan'].get('return_registers', ['v0'])))
                rows.append(report)
            except (ValueError, OSError) as exc:
                rows.append({'capture_sha256': record['sha256'], 'comparison': {'status': 'inconclusive'},
                             'reason': str(exc), 'authoritative': False})
        counts = dict(original.get('counts', {}))
        for row in rows:
            status = row['comparison']['status']
            counts[status] = counts.get(status, 0) + 1
        status = original['status']
        if any(r['comparison']['status'] == 'failed' for r in rows):
            status = 'observed_failure'
        elif status != 'observed_failure' and any(r['comparison']['status'] == 'inconclusive' for r in rows):
            status = 'inconclusive'
        elif status == 'unavailable' and any(r['comparison']['status'] == 'passed' for r in rows):
            status = 'observed_pass_with_execution_debt'
        result = {**original, 'source_sha256': key, 'status': status, 'counts': counts,
                  'semantic_key': [-counts.get('failed', 0), counts.get('passed', 0),
                                   -counts.get('inconclusive', 0), *original.get('semantic_key', [])],
                  'runtime_capture_results': rows, 'uncaptured_panel_status': original['status'],
                  'panel_sha256': evidence_schedule.fingerprint({'base': original.get('panel_sha256'),
                      'captures': [c['sha256'] for c in self.captures]}), 'authoritative': False}
        # Capture results are additional observations, never an equality waiver.
        result['feedback'] = [*original.get('feedback', []),
                              *[r for r in rows if r['comparison']['status'] != 'passed']]
        self.cache[key] = result
        return result
