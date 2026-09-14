import json
from pathlib import Path
import statistics
import time
import urllib.request

rows=[]
for _ in range(30):
    with urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=5) as response:
        rows.append(json.load(response))
    time.sleep(1)
report={'samples':rows,'gpu_mean':statistics.mean(r['gpu']['utilization'] for r in rows),
        'vram_peak_mib':max(r['gpu']['memory_used_mib'] for r in rows),
        'completed_delta':rows[-1]['metrics']['completed_items']-rows[0]['metrics']['completed_items']}
Path(__file__).with_name('live-validation.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='samples'}))
print(json.dumps({'status':rows[-1]['status'],'workers':rows[-1]['workers']}))
