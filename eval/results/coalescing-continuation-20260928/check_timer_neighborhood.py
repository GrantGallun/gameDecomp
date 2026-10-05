"""Check the existing one-step proposals for the newly successful timer form."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite import manifest
from solver import regalloc_mutations, repair_context

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--experiment', type=Path, required=True)
parser.add_argument('--winner', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
function = 'updateRacePlayerMode48AerialTrick'
path = args.experiment / 'run-02/t02_gradient/0/evolvability_coalesce/result.json'
result = json.loads(path.read_text())
source = result['best_source']
attempts = [json.loads(l) for l in (path.parent / 'attempts.jsonl').read_text().splitlines()]
receipt = next(r for r in attempts if r['source_sha256'] == manifest.digest(source.encode()) and r['compiled'])
work = path.parent / receipt['artifact']
evidence = {'compiled': True, 'verification': receipt['verification'], 'frontend': receipt['frontend'],
            'source_attribution': receipt['source_attribution']}
diff = (work / 'candidate_diff').read_text()

def body(text):
    start, end = repair_context.definition(text, function)
    return re.sub(r'\s+', '', text[start.end():end - 1])

winner_body = body(args.winner.read_text())
form = 'if(player->updateTimer<0x2D){player->updateTimer+=1;}'
families, offered, same_body, direct_form = Counter(), 0, [], []
for label, family, candidate in regalloc_mutations.variants(source, function, diff, evidence=evidence, coalesce=True):
    offered += 1
    families[family] += 1
    candidate_body = body(candidate)
    if candidate_body == winner_body:
        same_body.append(label)
    if form in candidate_body:
        direct_form.append(label)
summary = {'scope': 'full finite one-step generator on the actual searched parent; no reachability proof',
           'source_sha256': manifest.digest(source.encode()), 'offered': offered,
           'families': dict(families), 'same_body_ignoring_whitespace': same_body,
           'successful_direct_increment_form': direct_form,
           'winning_source_seen_in_search': any(manifest.digest(e['source'].encode()) ==
               manifest.digest(args.winner.read_bytes()) for e in [*result['events'], *result['resolutions']])}
manifest.write_json(args.output, summary)
print(json.dumps(summary, indent=2))
