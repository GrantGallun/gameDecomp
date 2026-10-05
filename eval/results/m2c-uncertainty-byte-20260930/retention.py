"""Exposed-panel passivity/trigger retention; no model or compiler trials."""
from pathlib import Path
import contextlib
import io
import json
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from solver import binary_type_draft as bd, m2c_uncertainty as uncertainty
from m2c import main
from solver.m2c_source_binding import SourceBoundObserver


def run():
    repo=Path('/home/grant/decomp/sbk1')
    parent=Path('/home/grant/decomp/experiments/m2c-campaign-transfer-20260930-v1')
    output=Path('/home/grant/decomp/experiments/m2c-uncertainty-byte-retention-20260930-v3')
    output.mkdir(exist_ok=False)
    selection=json.loads((parent/'selection.json').read_text())
    normal=bd._m2c
    rows=[]
    for fn in selection['functions']:
        calls=[]
        def observed(repo,assembly,context,scratch,*,valid_syntax):
            target,header=scratch/'target.s',scratch/'context.c'
            target.write_text(assembly);header.write_text(context)
            argv=['--target','mips-ido-c','--no-cache','--context',str(header)]
            if valid_syntax:argv.append('--valid-syntax')
            argv.append(str(target))
            stdout,stderr=io.StringIO(),io.StringIO()
            with SourceBoundObserver(source_files=[target]) as observer,contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                status=main.run(main.parse_flags(argv))
            baseline=normal(repo,assembly,context,scratch,valid_syntax=valid_syntax)
            assert status==baseline.returncode and (status!=0 or stdout.getvalue()==baseline.stdout)
            try:
                report=observer.verified_report(stdout.getvalue(),parent/'targets'/fn/'target.o',function=fn,function_address=selection['metadata'][fn]['addr'])
            except (ValueError,OSError) as exc:
                # Match the preparation runner: decline contradictory byte
                # guidance while preserving every ordinary C candidate.
                report=observer.report(stdout.getvalue())
                report.update(status='partial',binary_binding_error=str(exc),checked_target_words=0)
                for hazard in report['hazards']:
                    if hazard.get('instruction'):
                        hazard['instruction'].pop('word',None)
                        hazard['instruction'].pop('address',None)
            calls.append({'report':report,'stdout':stdout.getvalue(),'context_sha256':uncertainty.sha(context),
                          'valid_syntax':valid_syntax})
            return subprocess.CompletedProcess(argv,status,stdout.getvalue(),stderr.getvalue())
        bd._m2c=observed
        try:
            folder=output/fn;folder.mkdir()
            (folder/'target.s').write_text((parent/'targets'/fn/'target.s').read_text())
            variants,reports=bd.variants(repo,fn,folder)
        finally:
            bd._m2c=normal
        expected=json.loads((parent/'intake'/fn/'current'/'candidate-manifest.json').read_text())
        actual=[{'label':label,'source_sha256':uncertainty.sha(source)} for label,source in variants]
        (folder/'manifest-check.json').write_text(json.dumps({'actual':actual,'expected':expected,'calls':calls},indent=2))
        for label,source in variants:
            (folder/(label.replace(':','_')+'.c')).write_text(source)
        assert actual==expected, 'ordinary candidate retention changed: '+fn
        candidates=[]
        for label,source in variants:
            generation=next(r for r in reports if r.get('label')==label and r.get('source_sha256'))
            call=next(c for c in calls if c['valid_syntax']==generation['valid_syntax'] and
                      c['context_sha256']==generation['preprocessing']['preprocessed_sha256'])
            bound=uncertainty.rebind(source,call['report'],origin_source=call['stdout'])
            bound={**bound,'hazards':[r for r in bound['hazards'] if (r.get('instruction') or {}).get('byte_attribution_status')=='verified-target-word']}
            candidates.append({'label':label,'source_sha256':uncertainty.sha(source),
                               'packet':uncertainty.packet(source,bound)})
        row={'function':fn,'ordinary_candidates_equal':True,'observer_errors':sum(bool(c['report']['errors']) for c in calls),
             'candidates':candidates,'generation_calls':len(calls),
             'checked_target_words':sum(c['report']['checked_target_words'] for c in calls),
             'binding_failures':sum('binary_binding_error' in c['report'] for c in calls)}
        rows.append(row)
        (folder/'observation.json').write_text(json.dumps(row,indent=2))
    result={'functions':len(rows),'ordinary_candidates_equal':all(r['ordinary_candidates_equal'] for r in rows),
            'observer_errors':sum(r['observer_errors'] for r in rows),
            'checked_target_words':sum(r['checked_target_words'] for r in rows),
            'binding_failures':sum(r['binding_failures'] for r in rows),
            'functions_with_verified_regions':sum(any(c['packet']['regions'] for c in r['candidates']) for r in rows),
            'rows':rows,'training_eligible':False}
    for fn in ('drawScaledAssetTableSprite','initRacePlayerLandingSnowSpray'):
        row=next(r for r in rows if r['function']==fn)
        assert any(c['packet']['regions'] for c in row['candidates']), 'motivating residual did not fire: '+fn
    (output/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':run()
