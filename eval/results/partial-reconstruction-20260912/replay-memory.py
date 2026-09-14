"""Replay the real popup panel under the corrected linked seed policy.

Copies only the function workspace. Does not amend old experiment pins/history.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

from eval.semantic_lane import Panel
from solver import mips_differential as d, workspace

root = Path(__file__).resolve().parent
state = json.loads((root / 'popup-v3/state.json').read_bytes())
version = json.loads((root / 'popup-v3/version-0001.json').read_bytes())
work = Path('/home/grant/decomp/partial-memory-replay-20260912')
shutil.copytree(state['workspace'], work)
panel = Panel(Path(state['repo']), work, state['function'],
              state['semantic_cases'], state['semantic_steps'], state['exploration_cases'],
              header_source=version['manifest']['source'])
obj = work / Path(version['object']).name
assembly = workspace.semantic_assembly(
    obj.with_name(obj.stem + '_object_dump_normalized.s').read_text(), obj)
rows = d.run_suite(panel.target, assembly, panel.cases,
                   target_name=state['function'], candidate_name='unfinished-guard',
                   call_arities={**panel.arities, '__gd_unfinished': 1},
                   return_registers=panel.returns, max_steps=panel.max_steps,
                   callee_environment=panel.callee_environment)
result = {'runner_sha256': hashlib.sha256(Path(d.__file__).read_bytes()).hexdigest(),
          'previous_failure': version['semantic_unavailable'],
          'status': 'panel_built', 'panel': panel.report,
          'raw_comparison_counts': dict(Counter(row.status for row in rows)),
          'candidate_marker_cases': sum(any(call.callee == '__gd_unfinished'
                                           for call in row.candidate.calls) for row in rows),
          'authority': 'harness regression replay; unfinished candidate earns no correctness credit',
          'source_sha256': version['manifest']['source_sha256'],
          'workspace': str(work)}
(root / 'memory-replay.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k: v for k, v in result.items() if k != 'panel'}))
print(json.dumps({'target_execution': panel.target_execution,
                  'cases': len(panel.cases), 'debt': panel.debt}))
