"""Assemble a reviewable frozen-runtime update without changing live code."""
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'
DIRECT=['eval/fast_runtime.py','eval/target_work_cache.py','eval/fast_campaign.py',
        'eval/campaign_workers.py','eval/frozen_wavefront.py','eval/campaign_service.py',
        'eval/progress_app.py','eval/progress_app.html']
TESTS=['tests/test_target_work_cache.py','tests/test_controller_optimizations.py',
       'tests/test_prompt_budget.py','tests/test_fresh_compile.py']


def helper(filename):
    source=ROOT/'eval/results/optimization-audit-20260911'/filename
    spec=importlib.util.spec_from_file_location(source.stem,source)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.apply_frozen(STAGE)


def refresh():
    for relative in DIRECT+TESTS:
        shutil.copy2(ROOT/relative,STAGE/relative)
    # Include all main tests relevant to shared runtime behavior, retaining
    # frozen feature tests rather than importing unrelated later workflows.
    for relative in ('tests/test_campaign_fast.py','tests/test_campaign_service.py',
                     'tests/test_llm_cache.py','tests/test_worker_reuse.py'):
        if (ROOT/relative).exists():shutil.copy2(ROOT/relative,STAGE/relative)
    changed={}
    for path in STAGE.rglob('*'):
        if not path.is_file() or any(p in path.parts for p in ('__pycache__','.pytest_cache')):continue
        relative=path.relative_to(STAGE).as_posix()
        prior=LIVE/relative
        if not prior.exists() or prior.read_bytes()!=path.read_bytes():
            changed[relative]={'old_sha256':hashlib.sha256(prior.read_bytes()).hexdigest() if prior.exists() else None,
                               'new_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    (OUT/'staged-manifest.json').write_text(json.dumps(changed,indent=2)+'\n')
    print(json.dumps(changed,indent=2))


def main():
    shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    helper('stage_prompt_projection.py')
    helper('apply_frozen_reuse.py')
    refresh()


if __name__=='__main__':main()
