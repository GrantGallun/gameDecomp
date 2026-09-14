"""Stage reviewed impact fixes over the live frozen feature set."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def graft(original,current,names):
    source=current.splitlines(keepends=True)
    replacements={n.name:''.join(source[n.lineno-1:n.end_lineno]) for n in ast.parse(current).body
                  if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in names}
    assert set(replacements)==set(names)
    lines=original.splitlines(keepends=True)
    for n in reversed(ast.parse(original).body):
        if getattr(n,'name',None) in names:
            lines[n.lineno-1:n.end_lineno]=[replacements[n.name]]
    return ''.join(lines)

def main():
    refresh = '--refresh' in sys.argv
    if refresh:
        if not (OUT/'staged-manifest.json').is_file() or not STAGE.is_dir():
            raise ValueError('refresh requires existing staged release')
        for name in ('staged-manifest.json','staged-tests.log','main-tests.log'):
            archive=OUT/(name+'.before-opaque-fix')
            if (OUT/name).exists() and not archive.exists():shutil.copy2(OUT/name,archive)
    else:
        shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','results'))
    copies=['solver/mips_differential.py','solver/branch_context.py','solver/callee_execution.py','eval/semantic_lane.py',
            'eval/repair_yield.py','eval/fast_campaign.py',
            'tests/test_branch_context.py','tests/test_linked_memory_extents.py',
            'tests/test_repair_yield.py','tests/test_repair_queue.py','tests/test_word_pair_shifts.py',
            'tests/test_stress_work_budget.py','tests/test_differential_input_identity.py']
    for rel in copies:
        shutil.copy2(ROOT/rel,STAGE/rel)
    for rel,names in [('solver/modelrepair.py',{'semantic_prompt'}),
                      ('solver/repair_queue.py',{'lane','evidence_key','shared_issues'})]:
        (STAGE/rel).write_text(graft((LIVE/rel).read_text(),(ROOT/rel).read_text(),names))
    changed={}
    for path in STAGE.rglob('*'):
        if not path.is_file() or any(p in path.parts for p in ('__pycache__','.pytest_cache')):continue
        rel=path.relative_to(STAGE).as_posix()
        prior=LIVE/rel
        if not prior.exists() or sha(prior)!=sha(path):
            changed[rel]={'old_sha256':sha(prior) if prior.exists() else None,'new_sha256':sha(path)}
    (OUT/'staged-manifest.json').write_text(json.dumps(changed,indent=2))
    if not refresh:
        (STAGE/'eval/results').symlink_to(ROOT/'eval/results',target_is_directory=True)
    print(json.dumps(changed,indent=2))

if __name__=='__main__':main()
