"""Scoped regression suite with native/current code identity checks."""
import hashlib
import json
import os
from pathlib import Path
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
freeze = json.loads((OUT/'freeze.json').read_text())
CODE = ROOT if os.name == 'nt' else Path(freeze['code_root'])
sys.path.insert(0, str(CODE))
import pytest

TESTS = ['capability_potential', 'capability_map', 'repair_theory', 'theory_repairs',
         'theory_planner', 'repair_graph', 'repair_rules', 'repair_transitions',
         'repair_planner', 'storage_repairs', 'search_scheduler', 'search_replay',
         'search_evolution', 'search_pilot', 'regalloc_mutations', 'regalloc_search',
         'tool_boundary', 'tool_registry', 'header_signature_view', 'scalar_member_index',
         'wide_parameter_repair', 'wide_return_repair', 'wide_reconstruction']


class Counts:
    def __init__(self):
        self.outcomes = {'passed': 0, 'failed': 0, 'skipped': 0}

    def pytest_runtest_logreport(self, report):
        if report.when == 'call' or report.failed:
            self.outcomes[report.outcome] += 1


def check():
    for path, expected in {**freeze['files'], **freeze['test_fixtures']}.items():
        assert hashlib.sha256((CODE/path).read_bytes()).hexdigest() == expected, path


if __name__ == '__main__':
    check()
    counts = Counts()
    code = pytest.main(['-q', *[str(CODE/'tests'/f'test_{name}.py') for name in TESTS]], plugins=[counts])
    check()
    modules = {}
    for name in ('solver.capability_potential', 'solver.capability_operations', 'solver.capability_map',
                 'solver.capability_contracts', 'eval.theory_planner'):
        path = Path(sys.modules[name].__file__).resolve()
        relative = path.relative_to(CODE.resolve()).as_posix()
        modules[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        assert modules[relative] == freeze['files'][relative]
    result = {'platform': sys.platform, 'exit_code': int(code), 'outcomes': counts.outcomes,
              'modules': modules, 'tests': TESTS, 'code_root': str(CODE), 'new_compiler_calls': 0}
    (OUT/f'tests-{sys.platform}.json').write_text(json.dumps(result, indent=2))
    raise SystemExit(code)
