"""Read-only confirmation that retained jobs were transactionally imported."""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path

run = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908')
pre = json.loads(Path(__file__).with_name('post-lock-pre-resume.json').read_text())
pointer = json.loads((run / 'campaign.json').read_text())
service = json.loads((run / 'service.json').read_text())
ids = [j['id'] for j in pre['preserved_fast_inflight']]
with sqlite3.connect(f'file:{run / "campaign.sqlite"}?mode=ro', uri=True, timeout=5) as conn:
    imports = [dict(job_id=job_id, mapping=json.loads(mapping)) for job_id, mapping in conn.execute(
        'SELECT job_id,mapping FROM campaign_worker_imports WHERE job_id IN (?,?,?)', ids)]
with urllib.request.urlopen('http://172.28.32.1:11435/api/ps', timeout=5) as response:
    residency = json.load(response)
receipt = dict(checked_at=time.time(), before_checkpoint=pre['checkpoint'], checkpoint=pointer['commit'],
               before_completed=pre['completed_items'], completed=pointer['fast_metrics']['completed_items'],
               summary=pointer['summary'], service_status=service['status'],
               supervisor_pid=service['pid'], controller_pid=service['worker_pid'],
               batch_work_items=service['batch_work_items'], recovered_imports=imports,
               all_retained_jobs_imported=len(imports)==len(ids), model_residency=residency,
               limitation='Database-lock failure confirmed; external lock owner and distro restart cause unproven.')
Path(__file__).with_name('post-lock-live-validation.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt))
