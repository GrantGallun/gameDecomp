"""Audit the frozen new panel and report paired validity without hiding failures."""
from pathlib import Path
import collections
import hashlib
import json
import sqlite3
import statistics
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from solver import byte_certificate
from eval.agentrepair import _source_for_attempt

def read(path):return json.loads(path.read_text())
def sha(data):return hashlib.sha256(data).hexdigest()

P=HERE/'portable'
registration=read(P/'preregistration.json')
selection=read(P/'selection.json')
comparison=read(P/'comparison.json')
attempts=read(P/'attempts.json')
rows=comparison['rows']
assert len(selection['functions'])==len(set(selection['functions']))==64
assert len(rows)==128
assert registration['sample_selection']==selection
assert comparison['production_wiring'] is False and comparison['model_calls']==0
metadata=selection['metadata']
assert not set(selection['functions'])&set(selection['excluded_functions'])
assert not {r['tu_id'] for r in metadata.values()}&set(selection['excluded_tus'])
assert len({r['tu_id'] for r in metadata.values()})==45
assert max(collections.Counter(r['tu_id'] for r in metadata.values()).values())<=2
assert collections.Counter(selection['groups'].values())=={'broad':32,'address-pattern':32}
old=read(ROOT/'eval/results/m2c-campaign-connect-20260930/portable/preregistration.json')
adapter_name='eval/results/m2c-reconstruction-20260930/byte_address.py'
assert registration['code_sha256'][adapter_name]==old['code_sha256'][adapter_name]==selection['adapter_sha256']
for name,digest in registration['code_sha256'].items():
    frozen=(P/'code'/name).read_bytes()
    assert sha(frozen)==digest
    assert frozen.replace(b'\r\n',b'\n')==(ROOT/name).read_bytes().replace(b'\r\n',b'\n'),name
for feature in selection['binary_features']:
    fn=feature['function'];target=P/'targets'/fn
    receipt=read(HERE/'panel/targets'/fn/'target-receipt.json')
    assert receipt['original_sha256']==feature['assembly_sha256']
    assert sha((target/'target.o').read_bytes())==receipt['target_sha256']
    assert sha((target/'target.s').read_bytes())==sha((HERE/'panel/targets'/fn/'target.s').read_bytes())

conn=sqlite3.connect((P/'attempts.sqlite').as_uri()+'?mode=ro',uri=True)
conn.row_factory=sqlite3.Row
records={r['id']:r for r in conn.execute('SELECT * FROM attempts')}
assert len(records)==len(attempts)==comparison['compiler_calls']
assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]==0
assert conn.execute('SELECT COUNT(*) FROM inference').fetchone()[0]==0
edges={(r[0],r[1]) for r in conn.execute('SELECT parent_attempt_id,child_attempt_id FROM attempt_edges')}
certificates=0;children=[];provenance_issues=[];alternate_derivations=[]
for attempt in attempts:
    record=records[attempt['attempt_id']]
    assert record['source_sha256']==attempt['source_sha256']==sha(record['source_code'].encode())
    assert bool(record['compiled'])==attempt['compiled'] and record['score']==attempt['score']
    assert record['parent_attempt_id']==attempt['parent_attempt_id']
    parent_id=record['parent_attempt_id']
    if parent_id is not None:
        assert parent_id<record['id'] and (parent_id,record['id']) in edges
    sampling=json.loads(record['sampling'])
    assert sampling['training_eligible'] is False and sampling['production_wiring'] is False
    ws=P/'intake'/attempt['function']/attempt['arm']
    source=ws/Path(attempt['source_path']).name
    assert source.read_text().endswith(record['source_code'])
    assert sha((ws/'target.o').read_bytes())==attempt['target_sha256']
    frontend=sampling.get('frontend') or {}
    if frontend:
        assert frontend['source_sha256']==sha(source.read_bytes())
        assert (frontend.get('passed') is True)==attempt['frontend_passed']
    if attempt['compiled']:
        obj=ws/Path(attempt['object_path']).name
        cert=byte_certificate.certify(ws/'target.o',obj,source=record['source_code'])
        for key in ('source_sha256','target_sha256','candidate_sha256','exact'):
            assert cert[key]==attempt['certificate'][key]
        certificates+=1
    if ':byte-address:' in record['strategy']:
        reports=[r for r in sampling['binary_type_context'] if r.get('source_sha256')==record['source_sha256']]
        assert reports and sampling['assistance_tier']=='source-independent'
        assert parent_id is not None
        assert any(r.get('derived_from_sha256')==records[parent_id]['source_sha256'] for r in reports)
        for report in reports:
            # Multiple syntax/stride passes may emit identical child C from
            # different ordinary parents. Intake keeps one real edge and all
            # derivation reports; each alternative must have a scored parent.
            reported_parent=report['derived_from_sha256']
            assert any(a['function']==attempt['function'] and a['arm']==attempt['arm'] and
                a['source_sha256']==reported_parent and a['attempt_id']<record['id'] for a in attempts)
            if reported_parent!=records[parent_id]['source_sha256']:
                alternate_derivations.append({'attempt_id':record['id'],'actual_parent_attempt_id':parent_id,
                    'actual_parent_sha256':records[parent_id]['source_sha256'],'report_parent_sha256':reported_parent,
                    'report_label':report['label'],'strategy':record['strategy']})
            assert report['evidence']['elf_sha256']
            for observation in report['adapter_observations']:
                folder=ws/'generation'/Path(observation['provenance_path']).parent.name
                provenance=read(folder/'provenance.json')
                assert provenance['training_eligible'] is False
                assert sha((folder/'m2c.stdout').read_bytes())==observation['stdout_sha256']
                issues={'global':provenance['truncation'],'functions':[
                    {'name':f['name'],'errors':f['observation_errors'],'truncation':f['truncation'],
                     'translation_error':f['translation_error']} for f in provenance['functions']
                    if f['observation_errors'] or f['truncation'] or f['translation_error']]}
                if issues['global'] or issues['functions']:
                    provenance_issues.append({'attempt_id':record['id'],'issues':issues})
        children.append(record['id'])

gains=[];losses=[];unchanged=[];incomplete=[];paired=[];fired=[]
for fn in selection['functions']:
    pair={arm:next(r for r in rows if r['function']==fn and r['arm']==arm)
          for arm in ('current','byte-connected')}
    if pair['byte-connected']['generation_calls_with_byte_changes']:fired.append(fn)
    for arm,row in pair.items():
        ws=P/'intake'/fn/arm
        result=read(ws/'result.json')
        if row['selected_attempt_id'] is not None:
            selected=_source_for_attempt(conn,row['selected_attempt_id'],fn)
            assert selected==(ws/'result.best.c').read_text()
            assert sha(selected.encode())==row['source_sha256']==result['source_sha256']
            record=records[row['selected_attempt_id']]
            frontend=json.loads(record['sampling']).get('frontend') or {}
            assert row['usable_seed']==bool(record['compiled'] and frontend.get('passed') is True)
        else:
            assert not row['usable_seed']
        for retained in row['frontier']:
            assert sha(_source_for_attempt(conn,retained['attempt_id'],fn).encode())==retained['source_sha256']
        manifest=ws/'candidate-manifest.json'
        if not manifest.exists():
            assert row['completion_status']=='probe-incomplete'
    a,b=pair['current'],pair['byte-connected']
    original=read(P/'intake'/fn/'current/candidate-manifest.json')
    connected=read(P/'intake'/fn/'byte-connected/candidate-manifest.json')
    assert connected[:len(original)]==original
    if a['completion_status']=='probe-incomplete' or b['completion_status']=='probe-incomplete':
        incomplete.append({'function':fn,'group':selection['groups'][fn],
                           'arms':[{'arm':r['arm'],'error':r['error'],'compiler_calls':r['compiler_calls']}
                                   for r in pair.values() if r['completion_status']=='probe-incomplete'],
                           'any_valid_attempt_observed':{arm:any(x['compiled'] and x['frontend_passed']
                               for x in attempts if x['function']==fn and x['arm']==arm) for arm in pair}})
        continue
    paired.append(fn)
    if not a['usable_seed'] and b['usable_seed']:gains.append(fn)
    if a['usable_seed'] and not b['usable_seed']:losses.append(fn)
    if a['source_sha256'] is not None and a['source_sha256']==b['source_sha256']:unchanged.append(fn)

metrics={}
for group in ('broad','address-pattern','all'):
    selected_rows=[r for r in rows if group=='all' or r['sample_group']==group]
    metrics[group]={}
    for arm in ('current','byte-connected'):
        arm_rows=[r for r in selected_rows if r['arm']==arm]
        complete=[r for r in arm_rows if r['completion_status']!='probe-incomplete']
        arm_attempts=[a for a in attempts if a['arm']==arm and
            (group=='all' or selection['groups'][a['function']]==group)]
        metrics[group][arm]={'scheduled_functions':len(arm_rows),'complete_intakes':len(complete),
            'selected_valid_seeds':sum(r['usable_seed'] for r in complete),
            'functions_with_any_valid_attempt_within_budget':len({a['function'] for a in arm_attempts
                if a['compiled'] and a['frontend_passed']}),
            'compiler_calls':sum(r['compiler_calls'] for r in arm_rows),
            'generation_seconds':sum(r['generation_seconds'] for r in arm_rows),
            'total_seconds':sum(r['total_seconds'] for r in arm_rows),
            'median_total_seconds':statistics.median(r['total_seconds'] for r in arm_rows),
            'selected_exact':sum(r['exact'] for r in complete)}
conn.close()
result={'functions':64,'translation_units':45,'complete_pairs':len(paired),
        'gained_valid_seeds':gains,'lost_valid_seeds':losses,'identical_selected_sources_in_complete_pairs':unchanged,
        'incomplete_pairs':incomplete,'adapter_fired_functions':fired,'metrics':metrics,
        'attempts_audited':len(attempts),'certificates_rechecked':certificates,
        'byte_address_child_attempts':children,'provenance_issues':provenance_issues,
        'alternative_derivation_reports':alternate_derivations,
        'scope':'new functions/TUs for this adapter; exposed DEV pool, not project-wide or cross-game heldout',
        'production_wiring':False,'training_eligible':False}
write_path=HERE/'verification.json';write_path.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('functions','translation_units','complete_pairs','gained_valid_seeds',
    'lost_valid_seeds','incomplete_pairs','metrics','attempts_audited','certificates_rechecked')}))
