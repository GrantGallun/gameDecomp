"""Offline ordering comparison for TU-locality scheduling. Read-only.

Replays the scheduler's own selection over the live checkpoint with and without
`tu_index`. Each simulated pick only records a visit, so band rotation is
modelled but outcomes are not: this measures WHERE work goes, never how much of
it lands. No campaign file is written and no job is executed.
"""
import collections
import copy
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
sys.path.insert(0, str(ROOT))
from eval import campaign_state, completion_campaign as campaign  # noqa: E402
from solver import repair_queue  # noqa: E402

PICKS = 200


def simulate(state, picks):
    state = copy.deepcopy(state)
    order, switches, last = [], 0, None
    index = state.get('tu_index') or {}
    for _ in range(picks):
        chosen = campaign.choose(state)
        if chosen is None:
            break
        name, _profile = chosen
        unit = index.get(name) or UNITS.get(name)
        order.append((name, unit))
        if last is not None and unit != last:
            switches += 1
        last = unit
        # Only the visit is recorded: an unknown outcome must not be invented.
        state['nodes'][name].setdefault('jobs', []).append({'profile': 'simulated'})
    return order, switches


with sqlite3.connect(f"file:{(RUN / 'campaign.sqlite').as_posix()}?mode=ro", uri=True) as conn:
    UNITS = campaign.translation_units(conn)

live = campaign_state.read(RUN / 'campaign.json')
live.pop('tu_index', None)
indexed = copy.deepcopy(live)
indexed['tu_index'] = {name: unit for name, unit in UNITS.items() if name in live['nodes']}

pending_units = collections.Counter(
    indexed['tu_index'][n] for n, node in live['nodes'].items()
    if repair_queue.lane(node) not in {repair_queue.Lane.DONE, repair_queue.Lane.BLOCKED}
    and n in indexed['tu_index'])
near = {u for u, c in pending_units.items() if c <= 3}

report = {'picks': PICKS, 'units_with_work': len(pending_units),
          'near_finished_units_3_or_fewer': len(near)}
for label, state in (('baseline', live), ('locality', indexed)):
    order, switches = simulate(state, PICKS)
    units = [u for _, u in order]
    covered = {u for u in units if u in near}
    finished = sum(1 for u in covered if pending_units[u] <= collections.Counter(units)[u])
    report[label] = {'selected': len(order), 'distinct_units': len(set(units)),
                     'unit_switches': switches,
                     'near_finished_units_touched': len(covered),
                     'near_finished_units_fully_covered': finished,
                     'first_12': [f'{n} [{u}]' for n, u in order[:12]]}
(Path(__file__).parent / 'simulation.json').write_text(json.dumps(report, indent=1) + '\n')
print(json.dumps({k: v for k, v in report.items() if k not in ('baseline', 'locality')}, indent=1))
for label in ('baseline', 'locality'):
    print(label, json.dumps({k: v for k, v in report[label].items() if k != 'first_12'}))
