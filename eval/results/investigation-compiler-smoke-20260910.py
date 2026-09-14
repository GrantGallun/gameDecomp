from pathlib import Path
import json
import sqlite3
import tempfile
from solver.investigation import Tools

repo = Path('/home/grant/decomp/sbk1')
output = Path('/mnt/c/Code/gameDecomp/eval/results/investigation-compiler-smoke-20260910')
db = Path('/mnt/c/Code/gameDecomp/eval/results/kb-sbk1-rom-ranges-v1.sqlite')
with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as conn:
    function = 'updateControllerPakFileDeleteErrorPrompt'
    target = conn.execute('SELECT t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.name=?', (function,)).fetchone()[0]
    with tempfile.TemporaryDirectory(prefix='investigation-probe-identity-') as temporary:
        ws = Path(temporary)
        (ws / '.compiler-target.json').write_text(json.dumps({'function': function, 'target': target}))
        tools = Tools(repo, ws, conn, function, output)
        observed = json.loads(tools.evidence(function))
        result = json.loads(tools.probe('int probe(int x) { return x * 4; }',
                                      'IDO may lower multiplication by four to a left shift by two'))
        summary = {'compiled': result['compiled'], 'object_sha256': result.get('object_sha256'),
                   'observations': len(observed['observations']),
                   'callers': observed.get('callers'), 'callees': observed.get('callees'),
                   'shift_observed': 'sll' in result.get('disassembly', ''),
                   'scope': 'compiler and binary-evidence tool smoke; not game-function recovery'}
        (output / 'summary.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary))
