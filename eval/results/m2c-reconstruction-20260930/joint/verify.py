"""Read-only audit of retained trial artifacts and attempt logging."""
from pathlib import Path
import hashlib
import json
import sqlite3

P = Path(__file__).parent/'portable'
comparison = json.loads((P/'comparison.json').read_text())
rows = comparison['rows']
assert len(rows) == 12
conn = sqlite3.connect((P/'attempts.sqlite').as_uri()+'?mode=ro', uri=True)
assert conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 12
conn.close()
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for fn in {r['function'] for r in rows}:
    for arm in ('group2', 'group4'):
        assert sha(P/'drafts'/'alone2'/fn/'source.c') == sha(P/'drafts'/arm/fn/'source.c')
        assert sha(P/'drafts'/'alone2'/fn/'body-before-lowering.c') == sha(P/'drafts'/arm/fn/'body-before-lowering.c')
    for arm in ('alone2', 'group2', 'group4'):
        receipt = json.loads((P/'compiles'/arm/fn/'attempt-00001'/'receipt.json').read_text())
        row = next(r for r in rows if r['function'] == fn and r['arm'] == arm)
        assert receipt['compiled'] == row['compiled'] and receipt['exact'] == row['exact']
        assert receipt['source_sha256'] == sha(P/'drafts'/arm/fn/'source.c')
        assert receipt['target_sha256'] == sha(P/'targets'/fn/'target.o')
group2 = (P/'generation'/'group2'/'m2c.stdout').read_text()
group4 = (P/'generation'/'group4'/'m2c.stdout').read_text()
assert group2 == group4
joined = '\n'.join((P/'generation'/'alone2'/fn/'m2c.stdout').read_text().rstrip() for fn in [
    'drawMenuSprite', 'drawMenuSpriteClipped', 'drawMenuSpriteWithAlpha', 'drawMenuSpriteWithAlphaClipped'])
print(json.dumps({'audit': 'passed', 'attempts': 12, 'sources_identical_across_arms': True,
                  'group2_stdout_identical_group4': True, 'joined_alone_stdout_identical_group2_ignoring_whitespace': joined.split() == group2.split(),
                  'summary': comparison['summary']}))

S = P/'secondary'
secondary = json.loads((S/'comparison.json').read_text())
assert len(secondary['rows']) == 8
conn = sqlite3.connect((S/'attempts.sqlite').as_uri()+'?mode=ro', uri=True)
assert conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0] == 8
conn.close()
for row in secondary['rows']:
    fn, arm = row['function'], row['arm']
    receipt = json.loads((S/'compiles'/arm/fn/'attempt-00001'/'receipt.json').read_text())
    assert receipt['source_sha256'] == sha(S/'drafts'/arm/fn/'source.c')
    assert receipt['target_sha256'] == sha(S/'targets'/fn/'target.o')
    assert receipt['compiled'] == row['compiled'] and receipt['exact'] == row['exact']
    assert sha(S/'drafts'/'alone2'/fn/'body-before-lowering.c') == sha(S/'drafts'/'group2'/fn/'body-before-lowering.c')
    assert secondary['differences'][fn]['signatures']['alone2'] == secondary['differences'][fn]['signatures']['group2']
print(json.dumps({'secondary_audit': 'passed', 'secondary_attempts': 8, 'total_attempts': 20,
                  'body_and_definition_signatures_identical_across_arms': True,
                  'summary': secondary['summary']}))
