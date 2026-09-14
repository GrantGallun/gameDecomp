"""Exercise bounded worker recycling through multiple real spawn generations."""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import json
import multiprocessing
import os
from pathlib import Path
import time


def task(index):
    time.sleep(.02)
    return index,os.getpid()


def main():
    start=time.monotonic()
    submitted=0
    results=[]
    with ProcessPoolExecutor(max_workers=3,mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=8) as pool:
        futures=set()
        while len(results)<40:
            while submitted<40 and len(futures)<3:
                futures.add(pool.submit(task,submitted))
                submitted+=1
            done,_=wait(futures,timeout=30,return_when=FIRST_COMPLETED)
            if not done:
                raise RuntimeError('worker recycling stopped making progress')
            for future in done:
                results.append(future.result())
                futures.remove(future)
    counts={pid:sum(p==pid for _,p in results) for _,pid in results}
    assert sorted(i for i,_ in results)==list(range(40))
    assert max(counts.values())<=8 and len(counts)>=5
    report={'completed':40,'process_jobs':counts,'seconds':time.monotonic()-start}
    Path(__file__).with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
