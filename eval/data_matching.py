"""Inspect the campaign's pinned data catalog or compare candidate-emitted bytes.

Example: python -m eval.data_matching --run eval/results/resume-pipeline-20260908
Add --region ID --candidate-bytes data.bin to compare one emitted span. This
checks bytes only; it never marks a C definition, asset, or function matched.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import zlib

from solver import binary_data


def load(run):
    run = Path(run).resolve()
    pointer = json.loads((run/'campaign.json').read_bytes())
    store = (run/pointer['store']).resolve()
    if store.parent != run:
        raise ValueError('checkpoint store outside run')
    with closing(sqlite3.connect(store.as_uri()+'?mode=ro',uri=True)) as conn:
        raw = conn.execute('SELECT manifest FROM commits WHERE id=?',(pointer['commit'],)).fetchone()[0]
        if hashlib.sha256(raw).hexdigest() != pointer['sha256']:
            raise ValueError('checkpoint manifest checksum mismatch')
        manifest = json.loads(raw)
        blob = conn.execute('SELECT payload FROM objects WHERE hash=?',(manifest['metadata'],)).fetchone()[0]
    metadata = zlib.decompress(blob)
    if hashlib.sha256(metadata).hexdigest() != manifest['metadata']:
        raise ValueError('checkpoint metadata checksum mismatch')
    record = json.loads(metadata).get('binary_data_catalog')
    if not record:
        raise ValueError('campaign has not bound a data catalog yet')
    path = (run/record['path']).resolve()
    if not path.is_relative_to(run/'binary-data'):
        raise ValueError('data catalog outside run catalog directory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != record['sha256']:
        raise ValueError('data catalog checksum mismatch')
    return json.loads(raw), record, pointer['commit']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--list-regions',action='store_true')
    parser.add_argument('--region')
    parser.add_argument('--candidate-bytes',type=Path)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--out',type=Path)
    args = parser.parse_args()
    if bool(args.region) != bool(args.candidate_bytes):
        parser.error('--region and --candidate-bytes are required together')
    bundle, record, checkpoint = load(args.run)
    result = {'checkpoint':checkpoint,'catalog_sha256':record['catalog_sha256'],
              'summary':record['summary']}
    if args.list_regions:
        result['regions'] = [{k:r[k] for k in ('id','labels','section','storage','size','rom_verified','reconstruction')}
                             for r in bundle['catalog']['regions']]
    if args.region:
        result['comparison'] = binary_data.verify_candidate(bundle['catalog'],args.region,
            args.candidate_bytes.read_bytes(),offset=args.offset)
        result['candidate_path'] = str(args.candidate_bytes.resolve())
    text = json.dumps(result,indent=2)+'\n'
    if args.out:
        args.out.write_text(text)
    print(text,end='')


if __name__ == '__main__':
    main()
