"""Audit the real intake comparison, its receipts and failed first connection."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from solver import byte_certificate
from eval.agentrepair import _source_for_attempt


def read(path):
    return json.loads(path.read_text())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def audit(folder, *, final):
    registration = read(folder / 'preregistration.json')
    newline_only_changes = []
    assert registration['production_wiring'] is False
    assert registration['model_calls'] == 0 and registration['training_eligible'] is False
    for name, digest in registration['code_sha256'].items():
        frozen = (folder / 'code' / name).read_bytes()
        assert sha(frozen) == digest
        if final:
            current = (ROOT / name).read_bytes()
            if sha(current) != digest:
                assert current.replace(b'\r\n', b'\n') == frozen.replace(b'\r\n', b'\n'), name
                newline_only_changes.append(name)
    attempts = read(folder / 'attempts.json')
    conn = sqlite3.connect((folder / 'attempts.sqlite').as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    records = {r['id']: r for r in conn.execute('SELECT * FROM attempts')}
    assert len(attempts) == len(records)
    assert conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0] == 0
    assert conn.execute('SELECT COUNT(*) FROM inference').fetchone()[0] == 0
    edges = {(r[0], r[1]) for r in conn.execute('SELECT parent_attempt_id,child_attempt_id FROM attempt_edges')}
    compiled, certificates, pointer_children = 0, 0, []
    for attempt in attempts:
        record = records[attempt['attempt_id']]
        assert record['compiled'] == int(attempt['compiled'])
        assert record['source_sha256'] == attempt['source_sha256'] == sha(record['source_code'].encode())
        assert record['strategy'] == attempt['strategy']
        assert record['parent_attempt_id'] == attempt['parent_attempt_id']
        parent_id = record['parent_attempt_id']
        if parent_id is not None:
            assert parent_id != record['id'] and (parent_id, record['id']) in edges
            assert parent_id < record['id']
        sampling = json.loads(record['sampling'])
        assert sampling['training_eligible'] is False
        ws = folder / 'intake' / attempt['function'] / attempt['arm']
        source = ws / Path(attempt['source_path']).name
        # The ordinary scorer prepends only its Makefile C_DEFINES projection.
        assert source.read_text().endswith(record['source_code'])
        assert sha((ws / 'target.o').read_bytes()) == attempt['target_sha256']
        if sampling.get('frontend'):
            assert sampling['frontend']['source_sha256'] == sha(source.read_bytes())
            assert (sampling['frontend'].get('passed') is True) == attempt['frontend_passed']
        if attempt['compiled']:
            compiled += 1
            obj = ws / Path(attempt['object_path']).name
            recheck = byte_certificate.certify(ws / 'target.o', obj, source=record['source_code'])
            certificate = attempt['certificate']
            assert recheck['exact'] == certificate['exact'] == False
            for key in ('source_sha256', 'target_sha256', 'candidate_sha256'):
                assert recheck[key] == certificate[key]
            certificates += 1
        if ':byte-address:' in record['strategy']:
            assert parent_id is not None
            context = sampling['binary_type_context']
            reports = [r for r in context if r.get('source_sha256') == record['source_sha256']]
            assert reports and sampling['assistance_tier'] == 'source-independent'
            for report in reports:
                assert report['derived_from_sha256'] == records[parent_id]['source_sha256']
                assert report['adapter_observations'] and report['evidence']['elf_sha256']
                for observation in report['adapter_observations']:
                    assert observation['normalized_assembly_sha256']
                    assert observation['byte_address_changes'] or observation['late_pointer_store_views']
                    obs_path = ws / 'generation' / Path(observation['provenance_path']).parent.name
                    provenance = read(obs_path / 'provenance.json')
                    assert not provenance['training_eligible'] and not provenance['truncation']
                    assert provenance['functions']
                    for function in provenance['functions']:
                        assert not function['observation_errors'] and not function['truncation']
                        assert function['translation_error'] is None
                    assert sha((obs_path / 'm2c.stdout').read_bytes()) == observation['stdout_sha256']
            pointer_children.append(record['id'])
    if final:
        comparison = read(folder / 'comparison.json')
        rows = comparison['rows']
        assert len(rows) == 24 and len(attempts) == comparison['compiler_calls'] == 48
        unchanged, gained = [], []
        for function in registration['functions']:
            current = next(r for r in rows if r['function'] == function and r['arm'] == 'current')
            connected = next(r for r in rows if r['function'] == function and r['arm'] == 'byte-connected')
            for row in (current, connected):
                ws = folder / 'intake' / function / row['arm']
                result = read(ws / 'result.json')
                selected = _source_for_attempt(conn, row['selected_attempt_id'], function)
                assert selected == (ws / 'result.best.c').read_text()
                assert sha(selected.encode()) == result['source_sha256'] == row['source_sha256']
                selected_record = records[row['selected_attempt_id']]
                frontend = json.loads(selected_record['sampling']).get('frontend') or {}
                assert row['usable_seed'] == bool(selected_record['compiled'] and frontend.get('passed') is True)
                assert any(f['attempt_id'] == row['selected_attempt_id'] for f in result['frontier'])
                for retained in result['frontier']:
                    retrieved = _source_for_attempt(conn, retained['attempt_id'], function)
                    assert sha(retrieved.encode()) == retained['source_sha256']
            assert not current['usable_seed'] or connected['usable_seed']
            current_ws = folder / 'intake' / function / 'current'
            connected_ws = folder / 'intake' / function / 'byte-connected'
            originals = read(current_ws / 'candidate-manifest.json')
            connected_inputs = read(connected_ws / 'candidate-manifest.json')
            assert connected_inputs[:len(originals)] == originals
            if current['source_sha256'] == connected['source_sha256']:
                unchanged.append(function)
            if not current['usable_seed'] and connected['usable_seed']:
                gained.append(function)
        assert len(unchanged) == 10
        assert gained == ['drawMenuSpriteClipped', 'drawMenuSpriteWithAlphaClipped']
        totals = {arm: {'usable_seeds': sum(r['usable_seed'] for r in rows if r['arm'] == arm),
                        'compiler_calls': sum(r['compiler_calls'] for r in rows if r['arm'] == arm)}
                  for arm in ('current', 'byte-connected')}
    else:
        assert len(attempts) == 1
        totals, gained, unchanged = {}, [], []
    conn.close()
    return {'attempts_audited': len(attempts), 'compiled_attempts': compiled,
            'certificates_rechecked': certificates, 'byte_address_child_attempts': pointer_children,
            'current_workspace_newline_only_differences': newline_only_changes,
            'arm_totals': totals, 'gained_usable_seeds': gained, 'identical_selected_sources': unchanged}


result = {'completed': audit(HERE / 'portable', final=True),
          'initial_connection_failure': audit(HERE / 'portable/failed-connection', final=False)}
(HERE / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result))
