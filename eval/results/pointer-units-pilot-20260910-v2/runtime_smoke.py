import json
from pathlib import Path
import sqlite3
import sys

folder = Path(__file__).resolve().parent
sys.path.insert(0, str(folder/'runtime/code'))
from solver import repair

assert Path(repair.__file__).is_relative_to(folder/'runtime/code')
first = folder.parent/'pointer-units-pilot-20260910-v1'
name = 'func_800643B4'
entry = next(r for r in json.loads((first/'inventory.json').read_text())['functions'] if r['function'] == name)
source = Path(entry['source']).read_text()
assert repair._digest(source) == entry['source_sha256']
repo = first/name/'repo'
db = sqlite3.connect(first/'attempts.sqlite')
att, candidate, log = repair.search(repo, name, source, repo/'nonmatchings'/name, db,
    max_pairs=1, verbose=False, run_id='staged-pointer-runtime-smoke')
db.close()
result = {'module': repair.__file__, 'compiled': att.compiled, 'score': att.score,
          'object_exact': att.exact, 'frontend_passed': (att.frontend or {}).get('passed'),
          'source_sha256': repair._digest(candidate), 'receipt_id': att.receipt_id, 'log': log}
(folder/'runtime-smoke.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
assert att.exact and (att.frontend or {}).get('passed')
