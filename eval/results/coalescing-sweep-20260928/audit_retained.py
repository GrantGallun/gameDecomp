"""Read-only artifact audit: no compiler, campaign mutation, or reference C.

Run in WSL; output is a JSON analysis of the saved one-step sweep. Candidate
generation is replayed on the retained compiler-wrapped baseline, with exact
text equality against saved children checked before interpreting truncation.
"""
import collections
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from solver import byte_certificate, scalar_coalesce, regalloc_signature

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path)
args = parser.parse_args()
NATIVE = Path('/home/grant/decomp/experiments/coalescing-sweep-20260928/repos')
rows = [json.loads(line) for line in (HERE / 'sweep.jsonl').read_text().splitlines()]
tally = collections.Counter()
details = []
for row in rows:
    name = row['function']
    ws = NATIVE / name / 'nonmatchings' / name
    root = (ws / f'{name}_sbase.c').read_text()
    offered = list(scalar_coalesce.variants(root, name))
    replay_matches = len(offered) >= row['variants'] and all(
        offered[i][2] == (ws / f'{name}_sv{i}.c').read_text()
        for i in range(row['variants']))
    tally['functions'] += 1
    tally['generator_replays_match_all_saved_children'] += replay_matches
    if replay_matches:
        tally['functions_with_untried_variants_9_to_12'] += len(offered) > row['variants']
        tally['untried_variants_9_to_12'] += max(0, len(offered) - row['variants'])
    target = (ws / 'target_object_dump_normalized.s').read_text()
    baseline = (ws / f'{name}_sbase_object_dump_normalized.s').read_text()
    start_gradient = tuple(regalloc_signature.compare(target, baseline).gradient)
    gradients = [start_gradient]
    local = collections.Counter()
    for i in range(row['variants']):
        prefix = ws / f'{name}_sv{i}'
        obj = prefix.with_suffix('.o')
        local['attempts'] += 1
        if not obj.is_file():
            local['missing_object'] += 1
            continue
        source = prefix.with_suffix('.c').read_text()
        same = byte_certificate.certify(ws / f'{name}_sbase.o', obj, source=source)
        exact = byte_certificate.certify(ws / 'target.o', obj, source=source)
        local['same_object_as_parent'] += same['exact']
        local['target_certificate_exact'] += exact['exact']
        front = prefix.with_suffix('.frontend.json')
        frontend = json.loads(front.read_text()) if front.is_file() else None
        local['frontend_missing'] += frontend is None
        local['frontend_failed'] += frontend is not None and frontend.get('passed') is not True
        local['strict_exact'] += exact['exact'] and frontend is not None and frontend.get('passed') is True
        listing = Path(str(prefix) + '_object_dump_normalized.s')
        if listing.is_file():
            g = tuple(regalloc_signature.compare(target, listing.read_text()).gradient)
            gradients.append(g)
            local['gradient_better_than_root'] += g < start_gradient
            local['gradient_tied_to_root'] += g == start_gradient
            local['gradient_worse_than_root'] += g > start_gradient
        else:
            local['listing_missing'] += 1
    tally.update(local)
    tally['functions_with_gradient_progress'] += min(gradients) < start_gradient
    details.append({'function': name, 'saved_variants': row['variants'], 'replayed_variants': len(offered),
                    'replay_matches': replay_matches, 'baseline_gradient': start_gradient,
                    'best_gradient': min(gradients), **local})
report = {'kind': 'retained-coalescing-sweep-audit', 'scope': 'saved artifacts; no new compiles',
          'generator_sha256': hashlib.sha256(Path(scalar_coalesce.__file__).read_bytes()).hexdigest(),
          'metric_sha256': hashlib.sha256(Path(regalloc_signature.__file__).read_bytes()).hexdigest(),
          'certificate_code_sha256': hashlib.sha256(Path(byte_certificate.__file__).read_bytes()).hexdigest(),
          'sweep_rows_sha256': hashlib.sha256((HERE / 'sweep.jsonl').read_bytes()).hexdigest(),
          'counts': dict(tally), 'functions': details}
if args.output:
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
print(json.dumps(report['counts'], indent=2))
