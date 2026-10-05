"""Exercise the shared rewrite catalog on compiled nonmatching intake results."""
import hashlib
import json
import os
import itertools
import sqlite3
import sys
from pathlib import Path

ROOT = Path(os.environ.get('GAMEDECOMP_CODE_ROOT', Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(ROOT))
from eval.campaign_workers import isolate
from eval.intake_search import _preserves, _quality
from eval.tool_agent_run import _attempt_to_verdict
from solver import workspace, frontend_diagnostics, rewrites, regalloc_mutations, signals

OUT = Path(__file__).resolve().parent
NATIVE = Path.home() / 'decomp/experiments/clean-types-20260922'
REPO = Path.home() / 'decomp/sbk1'
baseline = json.loads((OUT / 'paired-frozen.json').read_text())
conn = sqlite3.connect(NATIVE / 'attempts.sqlite')
import argparse
ap = argparse.ArgumentParser()
ap.add_argument('--pilot', action='store_true')
args = ap.parse_args()
if not args.pilot:
    assert len(baseline['rows']) == 200
rows, attempts = [], 0
expected = 0
for original in baseline['rows']:
    if not (original['verdict']['compiled'] and not original['verdict']['exact']
            and original['frontend']['status'] == 'passed'):
        continue
    if args.pilot and original['function'] != 'drawEndingCreditsTumblingSnowboard':
        continue
    expected += 1
    name, source = original['function'], original['source']
    proposals = [(p.label,p.kind,p(source)) for p in rewrites.propose(source, original['verdict']['diff'])[:12]]
    profile = signals.analyse(original['verdict']['diff'],score=original['verdict']['score'])
    if (original['verdict']['score'] >= 99 and (profile.regalloc or profile.ordering)
            and not any(getattr(profile,axis) for axis in ('structural','width','offset','reloc','immediate'))):
        # Existing object-level generators, bounded to near-pure register/order
        # residuals. This is an experiment route, not a new solver mutation.
        extra = itertools.chain(regalloc_mutations.compound_assignments(source,name),
            regalloc_mutations.commutative_swaps(source,name),regalloc_mutations.declaration_swaps(source,name))
        proposals.extend(itertools.islice(extra,12))
    proposals = list({child:(label,kind,child) for label,kind,child in proposals if child!=source}.values())
    best, trace = original, []
    if proposals:
        native = isolate(REPO, NATIVE / 'byte-offset-builds' / name, name)
        ws = native / 'nonmatchings' / name
        for label,kind,child in proposals:
            att = workspace.score(ws, native, name, child, conn=conn, func=name,
                strategy='clean-types:object-rewrite', run_id='clean-types-20260922:object-rewrite',
                model='zero-model', parent_attempt_id=original['verdict']['receipt_id'], action=label, relation='repair')
            attempts += 1
            frontend = frontend_diagnostics.analyse(child, repo=native, target=original['before']['target'], full_diagnostics=True)
            node = dict(function=name, source=child, source_sha256=hashlib.sha256(child.encode()).hexdigest(),
                        verdict=_attempt_to_verdict(att), frontend=frontend)
            adopted = _preserves(node, best) and _quality(node) > _quality(best)
            trace.append(dict(action=label, kind=kind, changed=True, adopted=adopted, **node))
            if adopted:
                best = node
            if best['verdict']['exact']:
                break
    else:
        trace.append(dict(action='solver.rewrites.propose', changed=False,
            reason='no supported generator for the object diff'))
    row = {**best, 'before': original, 'trace': trace}
    rows.append(row)
    if proposals:
        print(json.dumps(dict(function=name, proposals=len(proposals), exact=best['verdict']['exact'], score=best['verdict']['score'])), flush=True)
    report = dict(rows=rows, attempts=attempts, expected=expected,
        code_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                     for name in ('solver/rewrites.py','solver/byte_view_order.py','solver/regalloc_mutations.py','solver/signals.py')})
    (OUT / ('object-pilot.json' if args.pilot else 'object-replay.json')).write_text(json.dumps(report, indent=2) + '\n')
assert len(rows) == expected
conn.close()
