"""Real target-panel cold/warm equality; isolated workspace, no model calls."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from eval import campaign_workers, fast_runtime, semantic_lane


def main():
    native=Path('/home/grant/decomp/target-work-validation-20260911')
    native.mkdir(exist_ok=True)
    results=[]
    for function in ('updateControllerPakFileDeleteErrorPrompt','initFallingActionProjectile'):
        repo=campaign_workers.isolate(Path('/home/grant/decomp/sbk1'),native/'repo',function)
        ws=repo/'nonmatchings'/function
        source=(ws/'base.c').read_text()
        pin=hashlib.sha256(b''.join((ROOT/name).read_bytes() for name in (
            'eval/fast_runtime.py','eval/target_work_cache.py','eval/semantic_lane.py',
            'solver/mips_differential.py'))).hexdigest()
        arms=[]
        previous=None
        for arm in ('cold','warm'):
            metrics=fast_runtime.install(native/'cache',pin,native/'model.lock')
            start=time.monotonic()
            try:
                panel=semantic_lane.Panel(repo,ws,function,header_source=source)
                record={'identity':panel.identity,'target':panel.target,'report':panel.report}
                normalized=json.loads(json.dumps(record,sort_keys=True))
                if previous is not None:
                    assert normalized==previous, function+' cold/warm panel mismatch'
                previous=normalized
                arms.append({'arm':arm,'seconds':time.monotonic()-start,'metrics':dict(metrics),
                             'identity':panel.identity,'cases':len(panel.cases)})
            finally:
                metrics.close()
        results.append({'function':function,'identical_full_report_and_cases':True,'arms':arms})
        print(json.dumps(results[-1]),flush=True)
    Path(__file__).with_name('target-work-benchmark.json').write_text(json.dumps(results,indent=2)+'\n')


if __name__=='__main__':main()
