"""Read the best retained candidate and its binary-derived residual; never reference C."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval.research_suite.manifest import digest
from solver import regalloc_signature, repair_context

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('result', type=Path)
parser.add_argument('--source', action='store_true')
args = parser.parse_args()
result = json.loads(args.result.read_text())
source = result['best_source']
sha = digest(source.encode())
rows = [json.loads(l) for l in (args.result.parent / 'attempts.jsonl').read_text().splitlines()]
row = next(r for r in rows if r['source_sha256'] == sha and r['compiled'])
listing = (args.result.parent / row['artifact'] / 'candidate_object_dump_normalized.s').read_text()
target = (args.result.parent / 'target.normalized.s').read_text()
report = regalloc_signature.compare(target, listing).to_dict()
print(json.dumps({'function': result['function'], 'artifact': row['artifact'], 'label': row['label'],
                  'source_sha256': sha, 'residual': report}, indent=2))
if args.source:
    start, end = repair_context.definition(source, result['function'])
    print(source[start.start():end])
