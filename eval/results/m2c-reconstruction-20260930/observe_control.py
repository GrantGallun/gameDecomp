"""Check that observing real DEV translations does not alter emitted C."""
import contextlib, hashlib, io, json
from pathlib import Path
from m2c import main
from byte_address import ByteAddresses
from provenance import ProvenanceCollector

here=Path(__file__).resolve().parent
(here/'observed').mkdir(exist_ok=True)
selection=json.loads((here/'portable/followup/selection.json').read_text())
rows=[]
for fn in selection['functions']:
    folder=here/'portable/followup/drafts'/fn/'byte-store-view'
    argv=['--target','mips-ido-c','--no-cache','--valid-syntax','--context',str(folder/'context.c'),str(folder/'input.s')]
    values=[]; report=None
    for observe in (False,True):
        out=io.StringIO()
        collector=ProvenanceCollector() if observe else contextlib.nullcontext()
        with collector,ByteAddresses(),contextlib.redirect_stdout(out):
            rc=main.run(main.parse_flags(argv))
        assert rc==0
        values.append(out.getvalue())
        if observe: report=collector.report()
    assert values[0]==values[1]
    functions=report['functions']
    (here/'observed'/(fn+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    rows.append({'function':fn,'stdout_identical':True,'stdout_sha256':hashlib.sha256(values[0].encode()).hexdigest(),
        'expressions':sum(len(f['expressions']) for f in functions),
        'instructions':sum(len(f['instructions']) for f in functions),
        'address_annotations':sum(bool(i['source_address']) for f in functions for i in f['instructions']),
        'truncation':report['truncation'],
        'function_truncation':{f['name']:f['truncation'] for f in functions},
        'observation_errors':[e for f in functions for e in f['observation_errors']]})
(here/'observer-verification.json').write_text(json.dumps({'rows':rows,'model_calls':0},indent=2)+'\n')
print(json.dumps({'functions_verified':len(rows),'expressions':sum(r['expressions'] for r in rows),
    'observation_errors':sum(len(r['observation_errors']) for r in rows),'stdout_identical':True}))
