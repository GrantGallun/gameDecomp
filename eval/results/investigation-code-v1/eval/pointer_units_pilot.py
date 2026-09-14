"""Read-only pending inventory and isolated deterministic pointer DEV replay."""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

from eval import agentrepair
from solver import address_units, frontend_repair, plateau, repair, workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-cases', type=int, default=8)
    parser.add_argument('--exclude-report', type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    original = Path('/home/grant/decomp/sbk1')
    campaign = ROOT / 'eval/results/resume-pipeline-20260908'
    snapshot = (campaign / 'campaign.json').read_bytes()  # WSL only; no Windows file lock.
    nodes = json.loads(snapshot)['nodes']
    inventory, candidates = [], []
    for name, node in sorted(nodes.items()):
        if node['status'] != 'pending':
            continue
        row = {'function': name, 'score': node.get('score'), 'status': 'unexamined', 'relations': []}
        inventory.append(row)
        diff = (node.get('residual') or {}).get('first_difference') or []
        pattern = r'^([-+])(\w+)\s+(\w+),\s*(\w+),\s*(-?0x[\da-fA-F]+|-?\d+)$'
        pairs = [re.match(pattern, line.strip()) for line in diff]
        for first, second in zip(pairs, pairs[1:]):
            if not first or not second or first[1] != '-' or second[1] != '+':
                continue
            if first.groups()[1:4] != second.groups()[1:4]:
                continue
            t, c = int(first[5], 0), int(second[5], 0)
            row['relations'].append({'target': t, 'candidate': c, 'base': first[4],
                'relations': address_units.numeric_relations(t, c, base=first[4])})
        path = Path(node.get('source') or '')
        ws = original / 'nonmatchings' / name
        if not path.is_file() or not (ws/'target.s').is_file():
            row.update(status='unavailable', reason='source or assembly unavailable')
            continue
        source = path.read_text()
        try:
            report = address_units.parameter_call_views(source, name, (ws/'target.s').read_text(),
                o32=frontend_repair.big_endian_o32(ws/'target.o'))
        except (ValueError, OSError, IndexError) as exc:
            row.update(status='unavailable', reason=str(exc))
            continue
        row.update(status='proposed' if report['changes'] else 'declined',
                   source=str(path), **{k: v for k, v in report.items() if k != 'source'})
        if report['changes']:
            candidates.append((name, source, report, node.get('score') or 0))
    (out/'inventory.json').write_text(json.dumps({'snapshot_sha256': hashlib.sha256(snapshot).hexdigest(),
        'scope': 'all pending saved sources; numeric relations use truncated first_difference only',
        'functions': inventory}, indent=2))
    counts = Counter(row['status'] for row in inventory)
    print(json.dumps({'inventory': dict(counts), 'pending': len(inventory)}), flush=True)
    candidates.sort(key=lambda row: (row[0] != 'func_800643B4', -row[3], row[0]))
    if args.exclude_report:
        excluded = {r['function'] for r in json.loads(args.exclude_report.read_text())['cases']}
        candidates = [row for row in candidates if row[0] not in excluded]
    selected = candidates[:args.max_cases]
    (out/'selection.json').write_text(json.dumps([{'function': n, 'source_sha256': r['source_sha256'],
        'candidate_sha256': r['candidate_sha256'], 'score': score} for n, s, r, score in selected], indent=2))
    db = sqlite3.connect(out/'attempts.sqlite')
    baseline = sqlite3.connect((ROOT/'eval/results/kb-sbk1-rom-ranges-v1.sqlite').as_uri()+'?mode=ro', uri=True)
    baseline.backup(db)
    baseline.close()
    results = {'status': 'running', 'regime': 'failure-enriched exposed DEV; assisted headers',
               'inventory': dict(counts), 'campaign_mutated': False, 'cases': []}
    for name, source, proposal, _ in selected:
        agentrepair._refuse_frozen_heldout(ROOT/'eval/sets', name)
        folder = out/name
        repo = folder/'repo'
        repo.mkdir(parents=True)
        for item in ('tools','include','src','asm','.venv','Makefile','symbol_addrs.txt',
                     'snowboardkids.yaml','snowboardkids.z64','build','undefined_syms_auto.txt','undefined_syms.txt'):
            path = original/item
            if path.exists():
                (repo/item).symlink_to(path, target_is_directory=path.is_dir())
        ws = repo/'nonmatchings'/name
        ws.mkdir(parents=True)
        for path in (original/'nonmatchings'/name).iterdir():
            if path.is_file() and (path.suffix == '.py' or path.name.startswith('target') or
                    path.name in {'build.sh','base.c','prelude.inc','.diff_algorithm'}):
                shutil.copy2(path, ws/path.name)
        (folder/'proposal.json').write_text(json.dumps(proposal, indent=2))
        parent = workspace.score(ws, repo, 'parent', source, conn=db, func=name,
                                 strategy='pointer-units-pilot-parent')
        candidate = proposal['source']
        att = workspace.score(ws, repo, 'candidate', candidate, conn=db, func=name,
            strategy='parameter-call-byte-units', parent_attempt_id=parent.receipt_id,
            action=json.dumps(proposal['changes']))
        exact = plateau.verified(repair._State(candidate, att))
        result = {'function': name, 'parent': asdict(parent), 'candidate': asdict(att),
            'verified_exact': exact, 'frontend_score_gain': bool((att.frontend or {}).get('passed') and att.score > parent.score)}
        results['cases'].append(result)
        (out/'report.json').write_text(json.dumps(results, indent=2))
        print(json.dumps({'function': name, 'before': parent.score, 'after': att.score,
            'frontend': (att.frontend or {}).get('passed'), 'exact': exact}), flush=True)
    db.close()
    results['status'] = 'complete'
    (out/'report.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
