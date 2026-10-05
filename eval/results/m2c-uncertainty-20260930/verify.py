"""Audit receipts, model guidance, byte associations and retained candidates.

Selected byte champion and available valid candidate are separate measures:
the existing repair quality order can prefer exact bytes with rejected C.
This audit never changes a scored source or the experiment's search policy.
"""
from pathlib import Path
import argparse
import hashlib
import io
import json
import sqlite3
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from solver import workspace, byte_certificate
from solver.function_boundary import text_extent


def sha(data):return hashlib.sha256(data).hexdigest()


def audit(parent, comparison, retention):
    registration=json.loads((comparison/'preregistration.json').read_text())
    outcome=json.loads((comparison/'comparison.json').read_text())
    preparation=json.loads((parent/'comparison.json').read_text())
    original_attempts=json.loads((parent/'attempts.json').read_text())
    new_attempts=json.loads((comparison/'attempts.json').read_text())
    attempts=original_attempts+new_attempts
    conn=sqlite3.connect(comparison/'attempts.sqlite');conn.row_factory=sqlite3.Row
    rows={r['id']:r for r in conn.execute('SELECT * FROM attempts')}
    assert len(rows)==len(attempts)
    certificates=0
    for result in attempts:
        row=rows[result['attempt_id']]
        source=row['source_code'];data=json.loads(row['sampling'])
        assert row['source_sha256']==sha(source.encode())==result['source_sha256']
        assert bool(row['compiled'])==result['compiled']
        assert data['training_eligible'] is False
        assert Path(result['source_path']).read_text()==workspace._candidate_compile_source(Path('/home/grant/decomp/sbk1'),source)
        frontend=data.get('frontend') or {}
        if frontend.get('source_sha256'):
            assert frontend['source_sha256']==sha(workspace._candidate_compile_source(Path('/home/grant/decomp/sbk1'),source).encode())
        if row['parent_attempt_id'] is not None:
            ancestor=rows[row['parent_attempt_id']]
            assert ancestor['id']<row['id'] and ancestor['func_addr']==row['func_addr']
        if result['compiled']:
            target=Path(result['source_path']).parent/'target.o'
            certificate=byte_certificate.certify(target,Path(result['object_path']),source=source)
            # Certificates contain tuple relocation identities; saved JSON
            # represents the same values as lists. Compare the whole JSON form.
            assert json.loads(json.dumps(certificate))==result['certificate']
            certificates+=1
    guidance_packets=0
    proposals=list(conn.execute('SELECT * FROM model_proposals'))
    for row in proposals:
        prompt=row['prompt_context']
        assert sha(prompt.encode())==row['prompt_sha256']
        parent_row=rows[row['parent_attempt_id']]
        marker='\nM2C RECONSTRUCTION UNCERTAINTY (source-bound, partial):\n'
        if row['run_id'].endswith(':uncertainty-model'):
            assert marker in prompt
            packet=json.loads(prompt.split(marker,1)[1].split('\n',1)[0])
            assert packet['source_sha256']==parent_row['source_sha256'] and packet['regions']
            guidance_packets+=1
        else:
            assert marker not in prompt
        if row['child_attempt_id'] is not None:
            child=rows[row['child_attempt_id']]
            assert child['parent_attempt_id']==row['parent_attempt_id']
        sampling=json.loads(row['sampling'])
        assert sampling.get('seed') is not None
    # Verify annotated original words against the assembled target, not merely
    # against another annotation. Synthetic/unmapped observations stay gaps.
    checked_words=0;unknown_words=0;observation_errors=0;passive_calls=0
    metadata=registration['selection']['metadata']
    for fn in registration['functions']:
        target=parent/'targets'/fn/'target.o'
        blob=target.read_bytes();section,size=text_extent(blob,fn)
        base=metadata[fn]['addr']
        base=int(base,0) if isinstance(base,str) else base
        for path in (parent/'intake'/fn/'current'/'generation').glob('*/receipt.json'):
            call=json.loads(path.read_text());report=call['uncertainty']
            assert call['ordinary_stdout_equal'] if 'ordinary_stdout_equal' in call else report['ordinary_stdout_equal']
            assert call['returncode']==0
            stdout=(path.parent/'m2c.stdout').read_text()
            assert report['source_sha256']==sha(stdout.encode())==call['stdout_sha256']
            assert sha((path.parent/'input.s').read_bytes())==call['normalized_assembly_sha256']
            assert sha((path.parent/'context.c').read_bytes())==call['preprocessed_sha256']
            observation_errors+=len(report['errors']);passive_calls+=1
            for hazard in report['hazards']:
                instruction=hazard.get('instruction') or {}
                if instruction.get('word') and instruction.get('address'):
                    offset=int(instruction['address'],16)-base
                    assert 0<=offset<=size-4 and section[offset:offset+4]==bytes.fromhex(instruction['word'])
                    checked_words+=1
                else:unknown_words+=1
    groups={}
    regressions=[]
    for group in sorted(set(registration['selection']['groups'].values())):
        scheduled=[r for r in outcome['rows'] if r['group']==group]
        stats={}
        for arm in registration['arms']:
            selected=[a for r in scheduled for a in r['arms'] if a['arm']==arm]
            available=0;available_exacts=0;improved=0
            for r in scheduled:
                calls=[a for a in new_attempts if a['function']==r['function'] and a['arm']==arm]
                valid=[a for a in calls if a['compiled'] and a['frontend_passed']]
                available+=bool(valid);available_exacts+=any(a['exact'] for a in valid)
                roots=[a for a in calls if a['parent_attempt_id']==next(
                    (b['selected_attempt_id'] for b in preparation['rows'] if b['function']==r['function']),None)]
                if roots and roots[0]['compiled'] and roots[0]['frontend_passed'] and not valid:
                    regressions.append([r['function'],arm])
                if roots and valid:
                    improved+=max(a['score'] for a in valid)>roots[0]['score']
            stats[arm]={'selected_usable':sum(a['usable'] for a in selected),
                        'available_usable':available,'available_exacts':available_exacts,
                        'selected_exacts':sum(a['exact'] for a in selected),
                        'valid_score_improvements':improved,
                        'new_compiles':sum(a['compiler_calls'] for a in selected),
                        'model_calls_reported':sum(a['model_calls'] for a in selected),
                        'model_proposals_logged':sum(p['run_id'].endswith(':'+arm) and
                            any(p['run_id']==r['function']+':'+arm for r in scheduled) for p in proposals),
                        'incomplete':sum(a['status']=='incomplete' for a in selected)}
        groups[group]={'scheduled':len(scheduled),'triggered':sum(r.get('triggered',False) for r in scheduled),'arms':stats}
    assert not regressions
    for relative,digest in registration['code_sha256'].items():
        assert sha((comparison/'code'/relative).read_bytes())==digest
        assert sha((ROOT/relative).read_bytes())==digest
    exposed=json.loads((retention/'result.json').read_text())
    assert exposed['functions']==64 and exposed['ordinary_candidates_equal'] and exposed['observer_errors']==0
    census=json.loads((ROOT/'eval/results/joint-reconstruction-20260930/census.json').read_text())
    old=json.loads((ROOT/'eval/results/m2c-campaign-connect-20260930/portable/selection.json').read_text())['functions']
    old+=json.loads((ROOT/'eval/results/m2c-campaign-transfer-20260930/portable/selection.json').read_text())['functions']
    assert not set(registration['functions'])&set(old)
    assert not set(registration['functions'])&set(census['heldout'])
    old_tus={census['metadata'][fn]['tu_id'] for fn in old}
    assert not {metadata[fn]['tu_id'] for fn in registration['functions'] if registration['selection']['groups'][fn]=='new-TU-broad'}&old_tus
    result={'audited_compiler_attempts':len(attempts),'compiled_certificates':certificates,
            'model_proposals':len(proposals),'guided_packets':guidance_packets,'passive_generation_calls':passive_calls,
            'original_words_checked':checked_words,'unverified_or_synthetic_associations':unknown_words,
            'observer_errors':observation_errors,'valid_seed_regressions':regressions,'groups':groups,
            'exposed_retention_functions':64,'production_wiring':False,'training_eligible':False,
            'proposal_statuses':dict(conn.execute('SELECT status,count(*) FROM model_proposals GROUP BY status'))}
    (comparison/'verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2));conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--parent',type=Path,required=True)
    parser.add_argument('--comparison',type=Path,required=True)
    parser.add_argument('--retention',type=Path,required=True)
    args=parser.parse_args();audit(args.parent,args.comparison,args.retention)
