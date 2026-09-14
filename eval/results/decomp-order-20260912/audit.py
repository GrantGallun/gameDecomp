"""Read-only replay of old/new ordering on the same saved campaign evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RUN = ROOT / 'eval/results/resume-pipeline-20260908'
sys.path.insert(0, str(OUT / 'staged-code'))
from eval import campaign_state, completion_campaign as campaign


def main():
    spec = importlib.util.spec_from_file_location('old_queue', RUN / 'code/solver/repair_queue.py')
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    state = campaign_state.read(RUN / 'campaign.json')
    before, _ = old.project(state, campaign.PROFILES)
    noindex, _ = campaign.repair_queue.project(state, campaign.PROFILES)
    ordered = lambda q: sorted(q['work_items'], key=lambda n: q['work_items'][n]['priority'])
    assert ordered(before) == ordered(noindex), 'old campaign ordering changed without index'
    with sqlite3.connect(f'file:{RUN / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        index = campaign.translation_units(conn)
    state['tu_index'] = {n: u for n, u in index.items() if n in state['nodes']}
    after, chosen = campaign.repair_queue.project(state, campaign.PROFILES)
    assert before['work_items'].keys() == after['work_items'].keys()
    for name, item in before['work_items'].items():
        new = after['work_items'][name]
        assert (item['lane'], item['profile'], item['evidence_key']) == (new['lane'], new['profile'], new['evidence_key'])
    timings = {}
    for label, module in [('before', old), ('after', campaign.repair_queue)]:
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            module.project(state, campaign.PROFILES)
            samples.append(time.perf_counter()-start)
        timings[label] = statistics.median(samples)
    def stats(queue):
        order = ordered(queue)
        ranks = {n: i for i, n in enumerate(order)}
        edges = [(a, b) for a, bs in state['dependency_graph']['callees'].items() for b in bs
                 if a in ranks and b in ranks
                 and queue['work_items'][a]['priority'][:2] == queue['work_items'][b]['priority'][:2]]
        return {'runnable': len(order), 'within_band_edges': len(edges),
                'caller_before_callee': sum(ranks[a] < ranks[b] for a, b in edges),
                'first_12': [{**queue['work_items'][n], 'cluster': state['tu_index'].get(n)} for n in order[:12]]}
    result = {'checkpoint': json.loads((RUN / 'campaign.json').read_bytes())['commit'],
              'index_sha256': campaign.digest(state['tu_index']), 'clusters': len(set(state['tu_index'].values())),
              'indexed_functions': len(state['tu_index']), 'eligible_profiles_evidence_unchanged': True,
              'no_index_order_unchanged': True, 'before': stats(before), 'after': stats(after),
              'median_projection_seconds': timings,
              'limit': 'Ordering replay only; no compiler/model calls or repair-yield claim.'}
    campaign_state.atomic(OUT / 'ordering-replay.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in {'before', 'after'}}))
    print(json.dumps({k: {x: y for x, y in v.items() if x != 'first_12'} for k, v in result.items() if k in {'before', 'after'}}))


if __name__ == '__main__':
    main()
