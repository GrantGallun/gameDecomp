"""Stage only the diagnostic prompt follow-up over the tested impact release."""
import importlib.util
import json
from pathlib import Path
import shutil

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
LIVE=OUT.parent/'resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'
spec=importlib.util.spec_from_file_location('impact_stage',OUT.parent/'impact-20260912/stage.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def main():
    shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','results'))
    rel='solver/modelrepair.py'
    (STAGE/rel).write_text(module.graft((LIVE/rel).read_text(),(ROOT/rel).read_text(),{'semantic_prompt'}))
    rel='solver/prompt_budget.py'
    shutil.copy2(ROOT/rel,STAGE/rel)
    for path in ROOT.joinpath('tests').glob('*prompt*compact*.py'):
        shutil.copy2(path,STAGE/'tests'/path.name)
    shutil.copy2(ROOT/'tests/test_prompt_budget.py',STAGE/'tests/test_prompt_budget.py')
    changed={}
    for path in STAGE.rglob('*'):
        if not path.is_file() or any(p in path.parts for p in ('__pycache__','.pytest_cache')):continue
        rel=path.relative_to(STAGE).as_posix()
        prior=LIVE/rel
        if not prior.exists() or module.sha(prior)!=module.sha(path):
            changed[rel]={'old_sha256':module.sha(prior) if prior.exists() else None,'new_sha256':module.sha(path)}
    (OUT/'staged-manifest.json').write_text(json.dumps(changed,indent=2))
    (STAGE/'eval/results').symlink_to(ROOT/'eval/results',target_is_directory=True)
    print(json.dumps(changed,indent=2))

if __name__=='__main__':main()
