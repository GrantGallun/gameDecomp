"""Recheck copied receipts, private attempt lineage, and exact certificates."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
ART = HERE/'portable'
sys.path.insert(0, str(HERE.parents[2]))
from solver import byte_certificate as certificate

def sha(data):
    return hashlib.sha256(data).hexdigest()

def main():
    comparison = json.loads((ART/'comparison.json').read_text())
    prereg = json.loads((ART/'preregistration.json').read_text())
    prior = HERE.parent/'joint-reconstruction-20260930'
    census = json.loads((prior/'census.json').read_text())
    assert not set(prereg['functions']) & set(census['heldout'])
    assert sha((ART/'probe-run.py').read_bytes()) == prereg['probe_sha256']
    assert sha((HERE/'probe.py').read_bytes()) == prereg['probe_sha256']
    conn = sqlite3.connect((ART/'attempts.sqlite').as_uri()+'?mode=ro', uri=True)
    attempts = {r[0]: r for r in conn.execute('SELECT id,source_sha256,compiled,exact,source_code,parent_attempt_id FROM attempts')}
    rows = comparison['rows']
    assert len(rows) == len(attempts) == 12
    assert len({(r['function'],r['arm']) for r in rows}) == 12
    assert conn.execute('SELECT COUNT(*) FROM inference').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == 0
    objects, checked, residuals = {}, 0, []
    parents = {r['function']:r['attempt_id'] for r in rows if r['arm'] == 'baseline'}
    for row in rows:
        work = ART/'compiles'/row['function']/row['arm']/PurePosixPath(row['receipt']).parent.name
        receipt = json.loads((work/'receipt.json').read_text())
        source = (work/'source.c').read_bytes()
        entry = attempts[row['attempt_id']]
        assert sha(source) == row['source_sha256'] == receipt['source_sha256'] == entry[1]
        assert entry[2:4] == (int(row['compiled']), int(row['exact']))
        assert sha(entry[4].encode()) == sha(source)
        assert entry[5] == (None if row['arm'] == 'baseline' else parents[row['function']])
        target = ART/'targets'/row['function']/'target.o'
        assert sha(target.read_bytes()) == receipt['target_sha256']
        if row['compiled']:
            candidate = work/'candidate.o'
            cert = receipt['verification']
            assert cert['source_sha256'] == sha(source)
            assert cert['target_sha256'] == sha(target.read_bytes())
            assert cert['candidate_sha256'] == sha(candidate.read_bytes())
            rerun = certificate.certify(target, candidate, source=source.decode())
            assert rerun['exact'] == cert['exact'] == row['exact']
            assert receipt['frontend']['source_sha256'] == sha(source)
            assert receipt['frontend']['passed'] is row['frontend_passed']
            objects[(row['function'],row['arm'])] = candidate
            checked += 1
    for fn in prereg['functions']:
        if 'Clipped' not in fn:
            images = [certificate.object_image(objects[(fn,arm)].read_bytes())['sections'] for arm in prereg['arms']]
            assert all(certificate.sections_equivalent(images[0],image) for image in images[1:])
        else:
            syntax = (ART/'compiles'/fn/'cursor-syntax/attempt-00001/candidate_object_dump_normalized.s').read_text().splitlines()
            byte = (ART/'compiles'/fn/'byte-units/attempt-00001/candidate_object_dump_normalized.s').read_text().splitlines()
            assert syntax[6].endswith(',0x7') and syntax[8].endswith(',0x80')
            assert byte[6].endswith(',0x3') and byte[8].endswith(',8')
            target_asm = (ART/'targets'/fn/'target.s').read_text()
            import re
            target_frame = re.search(r'addiu\s+\$sp,\s*\$sp,\s*(-0x[0-9A-Fa-f]+)', target_asm)[1]
            residuals.append({'function': fn, 'syntax_header_stride_bytes':128,
                             'byte_header_stride_bytes':8, 'target_header_stride_bytes':8,
                             'syntax_header_tail_bytes':128, 'byte_header_tail_bytes':8,
                             'target_stack_frame_bytes':-int(target_frame,16),
                             'candidate_stack_frame_bytes':-int(byte[0].split(',')[-1],16)})
    result = {'attempts_audited':12, 'certificates_rechecked':checked,
              'source_target_candidate_hashes_verified':True, 'probe_matches_frozen_run':True,
              'parent_edges_audited':8, 'wrapper_object_controls_equal':True,
              'inference_rows':0, 'evidence_rows':0, 'heldout_overlap':[],
              'residuals':residuals, 'summary':comparison['summary']}
    (HERE/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
