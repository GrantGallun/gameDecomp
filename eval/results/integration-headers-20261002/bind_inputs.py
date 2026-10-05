"""Bind retained candidates and their verified union for isolated integration probes."""
from pathlib import Path
import hashlib
import json
import sqlite3
import zlib

HERE = Path(__file__).resolve().parent
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
TARGETS = ('checkMainMenuSecretCode', 'addEndingActorShadowRenderCallback', 'drawScoreAttackChallengeLabels')


def main():
    pointer = json.loads((NATIVE / 'campaign.json').read_bytes())
    with sqlite3.connect((NATIVE / pointer['store']).as_uri() + '?mode=ro', uri=True) as conn:
        blob = conn.execute('SELECT manifest FROM commits WHERE id=?', (pointer['commit'],)).fetchone()[0]
        assert hashlib.sha256(blob).hexdigest() == pointer['sha256']
        manifest = json.loads(blob)
        metadata_raw = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?',
                                      (manifest['metadata'],)).fetchone()[0])
        assert hashlib.sha256(metadata_raw).hexdigest() == manifest['metadata']
        metadata = json.loads(metadata_raw)
        union = metadata['integration_sweep']['verified_union']
        entries = {}
        for name in sorted(set(union) | set(TARGETS)):
            digest = manifest['nodes'][name]
            raw = zlib.decompress(conn.execute('SELECT payload FROM objects WHERE hash=?', (digest,)).fetchone()[0])
            assert hashlib.sha256(raw).hexdigest() == digest
            node = json.loads(raw)
            source = Path(node['source']).read_text()
            assert hashlib.sha256(source.encode()).hexdigest() == node['source_sha256']
            entries[name] = {'function': name, 'source': node['source'], 'attempt_id': node['attempt_id'],
                             'verification': node['verification'], 'source_sha256': node['source_sha256'],
                             'status': node['status']}
            if name in TARGETS:
                (HERE / (name + '.candidate.c')).write_text(source)
        result = {'checkpoint': pointer['commit'], 'union': union, 'entries': entries,
                  'model_calls': 0, 'training_eligible': False,
                  'scope': 'Retained campaign candidates; mixed/unknown assistance lineage, no clean capability claim'}
        (HERE / 'inputs.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({'checkpoint': pointer['commit'], 'union_count': len(union),
                          'targets': {name: entries[name]['status'] for name in TARGETS}}, indent=2))


if __name__ == '__main__':
    main()
