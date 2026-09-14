"""Stage the bounded capture workflow over the current frozen runtime."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'
FILES=('eval/fast_campaign.py','eval/campaign_runtime.py','eval/project64_runner.py',
       'eval/project64_assets.json','eval/project64_capture_entry.js',
       'solver/project64_capture.py','solver/runtime_capture.py','eval/runtime_capture.py',
       'eval/captured_panel.py','tests/test_runtime_capture.py','tests/test_project64_capture.py',
       'tests/test_project64_runner.py','tests/test_campaign_runtime.py',
       'tests/test_campaign_capture_wiring.py')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

assert (OUT/'before-main/eval/fast_campaign.py').read_bytes()==(LIVE/'eval/fast_campaign.py').read_bytes()
if '--refresh' not in sys.argv:
    shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','results'))
    (STAGE/'eval/results').symlink_to(ROOT/'eval/results',target_is_directory=True)
else:
    assert STAGE.is_dir()
manifest={}
for rel in FILES:
    shutil.copy2(ROOT/rel,STAGE/rel)
    if sha(LIVE/rel)!=sha(STAGE/rel):
        manifest[rel]={'old_sha256':sha(LIVE/rel),'new_sha256':sha(STAGE/rel)}

def graft(rel, changes):
    # Always start from the frozen release, including --refresh. Main has
    # unrelated investigation/call-seed/cleanup machinery that is not part of
    # this amendment. Exact anchors make drift a hard error rather than an
    # accidental wholesale upgrade.
    original=(LIVE/rel).read_text()
    text=original
    for label,before,after in changes:
        assert text.count(before)==1, (rel,label,'frozen anchor changed')
        text=text.replace(before,after,1)
    compile(text,rel,'exec')
    (STAGE/rel).write_text(text)
    manifest[rel]={'old_sha256':sha(LIVE/rel),'new_sha256':sha(STAGE/rel),
                   'grafts':[label for label,_,_ in changes],
                   'scope':'runtime capture worker forwarding only; other frozen behavior retained'}

graft('eval/agentrepair.py',[
    ('runtime-captures-argument',
     '        semantic_cases: int = 64, semantic_steps: int = 10000) -> dict:',
     '        semantic_cases: int = 64, semantic_steps: int = 10000,\n'
     '        runtime_captures: tuple[dict, ...] = ()) -> dict:'),
    ('runtime-capture-config-identity',
     '        "constraint_plan_budget":8 if resilient else 0,\n    }',
     '        "constraint_plan_budget":8 if resilient else 0,\n    }\n'
     '    if runtime_captures:\n'
     "        config['runtime_captures'] = [record['sha256'] for record in runtime_captures]"),
    ('captured-panel-composition',
     '        panel = DeferredPanel(repo,ws,function,semantic_cases,semantic_steps)',
     '        panel = DeferredPanel(repo,ws,function,semantic_cases,semantic_steps)\n'
     '        if runtime_captures:\n'
     '            from eval.captured_panel import Panel as CapturedPanel\n'
     '            panel = CapturedPanel(panel, repo, ws, function, runtime_captures)'),
])
graft('eval/completion_campaign.py',[
    ('forward-function-runtime-captures',
     '        resilient=True,\n        strategy_brief=',
     '        resilient=True,\n'
     "        runtime_captures=tuple(config.get('runtime_captures', {}).get(function, [])),\n"
     '        strategy_brief='),
])
(OUT/'staged-manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps(manifest,indent=2))
