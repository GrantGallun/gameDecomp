"""No-model recheck: saved edits, whitespace-only anchoring, known failing input."""
from dataclasses import asdict
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from eval import frozen_wavefront, semantic_lane
from solver import mips_differential, modelrepair, repair_context, type_transaction, workspace


def main(*, allocator_state=False):
    root = Path(__file__).resolve().parents[3]
    original = Path('/home/grant/decomp/sbk1')
    previous = root / 'eval/results/repair-output-pilot-20260908-v2'
    out = root / ('eval/results/repair-output-allocator-recheck-20260908'
                  if allocator_state else 'eval/results/repair-output-recheck-20260908')
    out.mkdir(exist_ok=False)
    function = 'alSynSetFXMix'
    source = (previous / function / 'parent.c').read_text()
    historical = json.loads((root / 'eval/results/alSynSetFXMix-resilient-repair-v3.json').read_text())
    failing = historical['result']['semantic_validation']['feedback'][0]['input']
    case = mips_differential.TestCase(**{k: tuple(tuple(r) for r in v) if isinstance(v, list) else v for k,v in failing.items()})
    extra_cases = [case]
    allocator_evidence = None
    if allocator_state:
        allocator_asm = (original/'nonmatchings/__allocParam/target_object_dump_normalized.s').read_text()
        required = ('lw    a0,0x2c(v0)', 'lw    t6,0(a0)',
                    'sw    t6,0x2c(v0)', 'sw    zero,0(a0)')
        if not all(instruction in allocator_asm for instruction in required):
            raise ValueError('allocator fixture assembly evidence changed')
        allocator_evidence = {
            'target_assembly': allocator_asm,
            'sha256': hashlib.sha256(allocator_asm.encode()).hexdigest(),
            'derivation': 'alGlobals points at mapped player storage; +0x2c holds mapped arg2 free-list head; head next is null. Concrete allocator remains enabled; no call-return override.',
        }
        extra_cases = []
        for value in (1, 127, 128, 255):
            fixture = dict(failing)
            fixture['name'] = 'allocator-initialized-fxmix-' + str(value)
            fixture['global_writes'] = [*failing['global_writes'],
                ['alGlobals', 4, 0x10000000], ['@arg2', 4, 0]]
            fixture['player_writes'] = [[0x2c, 4, 0x12000000]]
            fixture['entry_registers'] = [['a1', 0x11000000], ['a2', value]]
            fixture['call_returns'] = []
            extra_cases.append(mips_differential.TestCase(**{
                k: tuple(tuple(r) for r in v) if isinstance(v, list) else v
                for k,v in fixture.items()}))
    live = json.loads((root / 'eval/results/resume-pipeline-20260908/campaign.json').read_text())
    frozen_wavefront.verify_files(live['pins'])
    report = {'kind':'saved-response-whitespace-and-regression-case-recheck','model_calls':0,
              'historical_regression_input': failing, 'rows':[],
              'allocator_state': allocator_state, 'allocator_evidence': allocator_evidence,
              'added_cases': [asdict(c) for c in extra_cases]}
    with tempfile.TemporaryDirectory(prefix='repair-output-recheck-') as temporary:
        repo = Path(temporary) / 'repo'
        repo.mkdir()
        for name in ('tools','include','src','asm','.venv','Makefile','symbol_addrs.txt',
                     'snowboardkids.yaml','snowboardkids.z64','build','undefined_syms_auto.txt','undefined_syms.txt'):
            if (original/name).exists():
                (repo/name).symlink_to(original/name,target_is_directory=(original/name).is_dir())
        dbpath = Path(temporary) / 'recheck.sqlite'
        shutil.copy2(root/'eval/results/kb-sbk1-rom-ranges-v1.sqlite',dbpath)
        db = sqlite3.connect(dbpath)
        ws = repo/'nonmatchings'/function
        ws.mkdir(parents=True)
        for path in (original/'nonmatchings'/function).iterdir():
            if path.is_file() and (path.suffix=='.py' or path.name.startswith('target') or path.name in {'build.sh','base.c','prelude.inc','.diff_algorithm'}):
                shutil.copy2(path,ws/path.name)
        parent = workspace.score(ws,repo,'parent',source,conn=db,func=function,strategy='repair-output-recheck-parent')
        panel = semantic_lane.Panel(repo,ws,function,max_cases=64,max_steps=3000,exploration_cases=128,header_source=source)
        panel.cases = tuple(panel.cases) + tuple(extra_cases)
        panel.identity = hashlib.sha256(json.dumps({'original_panel':panel.identity,'regression_cases':[asdict(c) for c in extra_cases]},sort_keys=True).encode()).hexdigest()
        panel.debt.append('includes one previously observed failure; development regression replay')
        panel.report.update(panel_sha256=panel.identity,cases=[asdict(c) for c in panel.cases],added_regression_cases=[asdict(c) for c in extra_cases])
        (out/'panel.json').write_text(json.dumps(panel.report,indent=2))
        candidates = [('parent',source)]
        validate = importlib.import_module('eval.experiments.campaign-gap-audit.repair_output_pilot').validate_output
        for arm in ('patch','full_function','compact_patch'):
            response = (previous/function/(arm+'.response.txt')).read_text()
            if arm == 'full_function':
                candidate = validate(source,function,response,arm)
            else:
                proposal = modelrepair.parse_proposal(response,source=source)
                candidate = modelrepair.apply_proposal(source,proposal,relaxed_whitespace=True)
                # Revalidate the exact resulting source through the same
                # function-boundary, signature and escape checks as the pilot.
                definition,end=repair_context.definition(candidate,function)
                candidate=validate(source,function,json.dumps({'kind':'other','hypothesis':'saved whitespace-only anchor replay',
                    'function':candidate[definition.start():end]}),'full_function')
            candidates.append((arm,candidate))
        for arm,candidate in candidates:
            attempt = parent if arm=='parent' else workspace.score(ws,repo,arm,candidate,conn=db,func=function,
                strategy='repair-output-recheck:'+arm,parent_attempt_id=parent.receipt_id)
            tag = 'parent' if arm=='parent' else arm
            semantic = panel(modelrepair.CandidateState(candidate,attempt,ws/(tag+'.o')))
            row={'arm':arm,'compiled':attempt.compiled,'frontend':attempt.frontend,'exact':attempt.exact,
                 'score':attempt.score,'semantic':semantic,'verification':attempt.verification}
            (out/(arm+'.c')).write_text(candidate)
            report['rows'].append(row)
            (out/'report.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'arm':arm,'exact':attempt.exact,'score':attempt.score,'semantic_counts':(semantic or {}).get('counts')}),flush=True)
        db.close()
        shutil.copy2(dbpath,out/'attempts.sqlite')
    frozen_wavefront.verify_files(live['pins'])
    report.update(status='complete',live_pins_unchanged_after=True)
    (out/'report.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--allocator-state', action='store_true')
    main(allocator_state=parser.parse_args().allocator_state)
