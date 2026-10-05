"""Read only the retained candidate and its existing diagnostic trace."""
from pathlib import Path
import sqlite3

PRIVATE = Path('/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace')
with sqlite3.connect((PRIVATE / 'attempts.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
    print(conn.execute('SELECT source_code FROM attempts WHERE id=157772').fetchone()[0])
print('\nTRACE FILES\n' + '\n'.join(str(p) for p in PRIVATE.iterdir()))
for p in (PRIVATE / 'repo/nonmatchings/drawControllerPakFileDeleteConfirmOptions').glob('.compiler-*.json'):
    print('\nRECIPE ' + str(p) + '\n' + p.read_text())
