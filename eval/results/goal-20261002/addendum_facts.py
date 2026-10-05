"""Facts recorded BEFORE the first sealed look (read-only)."""
import json, sqlite3, time
m = json.load(open('/mnt/c/Code/gameDecomp/eval/sets/sbk1_v5_sealed_nearmiss.json'))
camp = sqlite3.connect('file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro', uri=True)
kb = sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True)
cutoff = {'campaign_max_attempt_id': camp.execute('select max(id) from attempts').fetchone()[0],
          'kb_max_attempt_id': kb.execute('select max(id) from attempts').fetchone()[0], 'at': int(time.time())}
sealed = m['sealed']
ceil = sorted(r['function'] for r in sealed if r['best_score'] >= 100)
unknown = sorted(r['function'] for r in sealed if r['unknown_exact_rows'])
starts = {}
for r in sealed:
    starts[r['start']['ledger']] = starts.get(r['start']['ledger'], 0) + 1
out = {'cutoff': cutoff, 'sealed': len(sealed), 'score_100_ceiling_rows': ceil, 'unknown_exact_rows': unknown,
       'starts_by_ledger': starts,
       'dev_score_100': sum(r['best_score'] >= 100 for r in m['dev'])}
json.dump(out, open('/mnt/c/Code/gameDecomp/eval/results/goal-20261002/addendum.json', 'w'), indent=1)
print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in out.items()}))
