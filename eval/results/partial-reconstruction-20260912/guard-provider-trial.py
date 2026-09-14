"""One real model hypothesis, durably logged; no candidate acceptance here."""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.error

from eval import reconstruct as r
from eval.fast_runtime import model_lease
from kb import attempts
from solver import llm

root = Path('eval/results/partial-reconstruction-20260912')
state = json.loads((root/'popup-v4/state.json').read_text())
parent = r.version(state)
plan = r.entry_guard_plan(parent['manifest'], 0)
prompt = r.guard_prompt(parent, plan)
settings = dict(timeout=240, num_thread=12, num_predict=6000, think='low',
                temperature=0.2, seed=9, num_ctx=32768, response_schema=r.GUARD_SCHEMA,
                transport_attempts=1)
result = {'prompt': prompt, 'settings': settings, 'plan': plan,
          'parent_attempt_id': parent['attempt']['receipt_id'], 'private_db': state['db'],
          'code_sha256': hashlib.sha256(Path(r.__file__).read_bytes()).hexdigest()}
start = time.monotonic()
try:
    with model_lease(Path('/home/grant/decomp/campaign-workers-20260911/model.lock')):
        raw, metadata = llm.generate('http://172.28.32.1:11435', 'gpt-oss:20b', prompt, **settings)
    result.update(raw=raw, metadata=metadata, wall_seconds=time.monotonic()-start)
    with sqlite3.connect(state['db']) as conn:
        result['proposal_id'] = attempts.record_model_proposal(conn, run_id='entry-guard-provider-20260912',
            parent_attempt_id=parent['attempt']['receipt_id'], prompt=prompt, raw_response=raw,
            status='proposed', model='gpt-oss:20b', kind='partial-entry-guard',
            sampling={**settings, 'metadata': metadata, 'plan': plan},
            wall_ms=int(result['wall_seconds']*1000), token_cost=metadata.get('eval_count', 0))
    result['normalized_proposal'] = r.guard_proposal(plan, json.loads(raw))
    r.partial.apply(parent['manifest'], result['normalized_proposal'])
    result['status'] = 'valid_proposal_uncompiled'
    (root/'guard-model-proposal.json').write_text(json.dumps(result['normalized_proposal'], indent=2))
except Exception as exc:
    result['status'] = 'error'
    result['error'] = repr(exc)
    if isinstance(exc, urllib.error.HTTPError):
        result['error_body'] = exc.read().decode(errors='replace')
finally:
    (root/'guard-provider-trial.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k: result.get(k) for k in ('status', 'error', 'error_body', 'proposal_id', 'wall_seconds', 'raw')}))
