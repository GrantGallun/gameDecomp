"""Read-only SBK1 recipe coverage; never reads game C bodies or SBK2 inputs."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path
import json
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from solver import compiler_recipe

repo = Path('/home/grant/decomp/sbk1')
with sqlite3.connect('file:/home/grant/decomp/kb-sbk1.sqlite?mode=ro', uri=True) as conn:
    rows = conn.execute('SELECT t.name,t.object_path,COUNT(f.addr) FROM tus t '
                        'LEFT JOIN functions f ON f.tu_id=t.id GROUP BY t.id ORDER BY t.id').fetchall()

def inspect(row):
    name, object_path, count = row
    target = object_path if object_path is not None else name
    record = {'tu': name, 'target': target, 'function_count': count}
    try:
        selected = compiler_recipe.resolve(repo, target)
        record.update(status='supported', recipe=selected)
    except (OSError, ValueError) as exc:
        record.update(status='unavailable', error_type=type(exc).__name__, reason=str(exc))
        if hasattr(exc, 'evidence'):
            record['evidence'] = exc.evidence
    return record

with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(inspect, rows))
summary = Counter()
for row in results:
    summary[row['status']] += 1
    if row['status'] != 'supported':
        summary[row['error_type'] + ': ' + row['reason']] += 1
report = {'game': 'sbk1', 'compiler': 'existing SBK1 IDO recipe only',
          'scope': 'TU recipe resolution; no candidate or target compiler invocation',
          'makefile_sha256': compiler_recipe.sha((repo / 'Makefile').read_bytes()),
          'adapter_sha256': compiler_recipe.sha(Path(compiler_recipe.__file__).read_bytes()),
          'summary': dict(summary), 'rows': results}
(HERE / 'coverage.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report['summary']), flush=True)
