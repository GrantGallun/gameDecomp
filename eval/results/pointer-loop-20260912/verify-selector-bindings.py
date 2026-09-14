"""Bind extra-case receipts to unchanged logged private source/object inputs."""
import hashlib
import json
from pathlib import Path

folder = Path('/home/grant/decomp/pointer-loop-20260912/drawCharacterSelectCoursePreviewFrame-1789251567269010535')
summary = json.loads((folder / 'summary.json').read_bytes())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
rows = []
for index in (0, 21, 13):
    row = summary['attempts'][index]
    assert sha(folder / ('attempt-%03d.source.c' % index)) == row['source_sha256']
    consumed = [artifact for name, artifact in row['artifacts'].items()
                if name.endswith('.o') or name.endswith('_object_dump_normalized.s')]
    assert len(consumed) >= 2
    for artifact in consumed:
        assert sha(Path(artifact['path'])) == artifact['sha256']
    rows.append({'index': index, 'source_sha256': row['source_sha256'], 'source_hash_matches': True,
                 'consumed_artifact_hashes_match': True, 'artifacts': consumed})
out = Path(__file__).resolve().parent / 'selector-diagnostics/binding-verification.json'
out.write_text(json.dumps({'results': rows}, indent=2) + '\n')
print(json.dumps({'verified_indices': [row['index'] for row in rows]}))
