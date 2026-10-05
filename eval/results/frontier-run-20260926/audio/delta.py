"""Show only the generated source delta and first baseline assembly hunk."""
import difflib
import sqlite3

conn = sqlite3.connect('/home/grant/decomp/experiments/frontier-run-20260926/audio/attempts.sqlite')
before, diff = conn.execute('select source_code,diff_summary from attempts where id=1').fetchone()
after = conn.execute('select source_code from attempts where id=14').fetchone()[0]
lines = diff.splitlines()
first = next((i for i, line in enumerate(lines) if line.startswith('@@')), 0)
print('BASELINE_ASSEMBLY')
print('\n'.join(lines[first:first + 16]))
print('GENERATED_SOURCE_DELTA')
print('\n'.join(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm='')))
