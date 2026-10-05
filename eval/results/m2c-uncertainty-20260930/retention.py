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


def run():
    repo=Path('/home/grant/decomp/sbk1')
    parent=Path('/home/grant/decomp/experiments/m2c-campaign-transfer-20260930-v1')
    output=Path('/home/grant/decomp/experiments/m2c-uncertainty-retention-20260930-v1')
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
            with uncertainty.Observer() as observer,contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                status=main.run(main.parse_flags(argv))
            baseline=normal(repo,assembly,context,scratch,valid_syntax=valid_syntax)
            assert status==baseline.returncode and (status!=0 or stdout.getvalue()==baseline.stdout)
            report=observer.report(stdout.getvalue())
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
        assert actual==expected, 'ordinary candidate retention changed: '+fn
        candidates=[]
        for label,source in variants:
            generation=next(r for r in reports if r.get('label')==label and r.get('source_sha256'))
            call=next(c for c in calls if c['valid_syntax']==generation['valid_syntax'] and
                      c['context_sha256']==generation['preprocessing']['preprocessed_sha256'])
            bound=uncertainty.rebind(source,call['report'],origin_source=call['stdout'])
            candidates.append({'label':label,'source_sha256':uncertainty.sha(source),
                               'packet':uncertainty.packet(source,bound)})
        row={'function':fn,'ordinary_candidates_equal':True,'observer_errors':sum(bool(c['report']['errors']) for c in calls),
             'candidates':candidates,'generation_calls':len(calls)}
        rows.append(row)
        (folder/'observation.json').write_text(json.dumps(row,indent=2))
    result={'functions':len(rows),'ordinary_candidates_equal':all(r['ordinary_candidates_equal'] for r in rows),
            'observer_errors':sum(r['observer_errors'] for r in rows),'rows':rows,'training_eligible':False}
    (output/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':run()
