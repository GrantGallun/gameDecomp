"""Scoped regressions, with code/test identities checked against the freeze."""
import hashlib
import json
import os
from pathlib import Path
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
frozen=json.loads((OUT/'freeze.json').read_text())
CODE=ROOT if os.name=='nt' else Path(frozen['code_root'])
sys.path.insert(0,str(CODE))
import pytest

TESTS=['capability_map','repair_theory','theory_repairs','theory_planner','repair_graph','repair_rules',
       'repair_transitions','repair_planner','storage_repairs','search_scheduler','search_replay','search_evolution',
       'search_pilot','regalloc_mutations','regalloc_search','tool_boundary','tool_registry',
       'header_signature_view','scalar_member_index','wide_parameter_repair','wide_return_repair','wide_reconstruction']


class Counts:
    def __init__(self):self.outcomes={'passed':0,'failed':0,'skipped':0}
    def pytest_runtest_logreport(self,report):
        if report.when=='call' or report.failed:self.outcomes[report.outcome]+=1


if __name__=='__main__':
    counts=Counts()
    paths=[CODE/'tests'/f'test_{name}.py' for name in TESTS]
    for path in paths:
        assert hashlib.sha256(path.read_bytes()).hexdigest()==frozen['files'][path.relative_to(CODE).as_posix()]
    fixtures=json.loads((OUT/'test-fixtures.json').read_text())
    for path,sha in fixtures.items():
        assert hashlib.sha256((CODE/path).read_bytes()).hexdigest()==sha
    code=pytest.main(['-q',*[str(p) for p in paths]],plugins=[counts])
    identities={}
    for name in ('solver.capability_map','solver.capability_contracts','eval.theory_planner','eval.repair_planner'):
        if name not in sys.modules:continue
        path=Path(sys.modules[name].__file__).resolve()
        assert path.is_relative_to(CODE.resolve())
        relative=path.relative_to(CODE).as_posix()
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        assert sha==frozen['files'][relative]
        identities[relative]=sha
    result={'platform':sys.platform,'exit_code':int(code),'outcomes':counts.outcomes,
            'tests':TESTS,'module_sha256':identities,'code_root':str(CODE),'new_compiler_calls':0}
    result['test_fixture_sha256']=fixtures
    (OUT/f'tests-{sys.platform}.json').write_text(json.dumps(result,indent=2))
    raise SystemExit(code)
