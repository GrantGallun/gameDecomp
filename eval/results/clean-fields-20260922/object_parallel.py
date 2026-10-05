"""Four disjoint object-rewrite shards, merged only after complete verification."""
import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT=Path(__file__).resolve().parent
workers=min(4,len(os.sched_getaffinity(0)))
baseline=json.loads((OUT/'paired-frozen.json').read_text())
assert len(baseline['rows'])==200

def run(index):
    with (OUT/f'object-worker-{index}.log').open('w') as log:
        result=subprocess.run([sys.executable,str(OUT/'object_replay.py'),
            '--shards',str(workers),'--shard',str(index)],stdout=log,stderr=subprocess.STDOUT)
    assert result.returncode==0,index
    receipt=json.loads((OUT/f'object-replay-shard-{index}.json').read_text())
    assert len(receipt['rows'])==receipt['expected']
    print(f'object worker {index}: {len(receipt["rows"])} states, {receipt["attempts"]} children',flush=True)
    return receipt

with ThreadPoolExecutor(max_workers=workers) as pool:
    receipts=list(pool.map(run,range(workers)))
rows=[r for receipt in receipts for r in receipt['rows']]
names=[r['function'] for r in baseline['rows'] if r['verdict']['compiled']
    and not r['verdict']['exact'] and r['frontend']['status']=='passed']
assert len(rows)==len({r['function'] for r in rows})==len(names)
assert {r['function'] for r in rows}==set(names)
assert all(r['code_sha256']==receipts[0]['code_sha256'] for r in receipts)
rows.sort(key=lambda r:names.index(r['function']))
result=dict(rows=rows,expected=len(names),attempts=sum(r['attempts'] for r in receipts),
    workers=workers,code_sha256=receipts[0]['code_sha256'],
    driver_sha256={n:hashlib.sha256((OUT/n).read_bytes()).hexdigest()
        for n in ('object_replay.py','object_parallel.py')})
(OUT/'object-replay.json').write_text(json.dumps(result,indent=2)+'\n')
print(f'Object pass complete: {len(rows)} states, {result["attempts"]} logged children',flush=True)
