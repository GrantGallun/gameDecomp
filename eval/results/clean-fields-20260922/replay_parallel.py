"""Resume four disjoint experiment shards and verify their complete merge."""
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
workers = min(4, len(os.sched_getaffinity(0)))
started = time.monotonic()
serial = OUT/'paired-frozen.json'
if serial.exists():
    (OUT/'paired-frozen-serial-seed.json').write_bytes(serial.read_bytes())


def run(index):
    log = OUT/f'worker-{index}.log'
    with log.open('w') as stream:
        result = subprocess.run([sys.executable,str(OUT/'replay.py'),'--phase','search',
            '--tag','frozen','--shards',str(workers),'--shard',str(index)],
            cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    assert result.returncode == 0, f'worker {index} failed; see {log}'
    receipt = json.loads((OUT/f'paired-frozen-shard-{index}.json').read_text())
    assert len(receipt['rows']) == receipt['expected']
    print(f'worker {index}: {len(receipt["rows"])} functions complete',flush=True)
    return receipt


with ThreadPoolExecutor(max_workers=workers) as pool:
    futures = [pool.submit(run,index) for index in range(workers)]
    receipts = [future.result() for future in as_completed(futures)]
first = receipts[0]
assert all(r['code_sha256'] == first['code_sha256'] and r['evidence'] == first['evidence'] for r in receipts)
rows = [row for receipt in receipts for row in receipt['rows']]
names = [r['function'] for r in json.loads((OUT/'baseline-reviewed.json').read_text())['rows']]
assert len(rows) == len(set(r['function'] for r in rows)) == len(names) == 200
assert set(r['function'] for r in rows) == set(names)
rows.sort(key=lambda r:names.index(r['function']))
result = dict(rows=rows,expected=200,code_sha256=first['code_sha256'],evidence=first['evidence'],
    attempt_db=first['attempt_db'],workers=workers,seconds=time.monotonic()-started,
    driver_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest()
                   for name in ('replay.py','replay_parallel.py')})
serial.write_text(json.dumps(result,indent=2)+'\n')
print('All 200 functions merged with matching implementation and evidence hashes.',flush=True)
