"""Explain remaining void-member declines without changing proposals."""
import json
import sys
from collections import Counter
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import void_field_repair

OUT = Path(__file__).resolve().parent
text = (ROOT / 'solver/void_field_repair.py').read_text()
needle = "if not peers or len({(p['width'],p['type']=='f32') for p in peers})!=1:continue"
replacement = """if not peers or len({(p['width'],p['type']=='f32') for p in peers})!=1:
                report.setdefault('inspection', []).append({'base':base,'offset':offset,'line':number,
                    'reason':'missing' if not peers else 'ambiguous','local':bool(locals_),
                    'peers':peers})
                continue"""
assert needle in text
namespace = dict(void_field_repair.__dict__)
exec(text.replace(needle, replacement), namespace)
rows = []
for entry in json.loads((OUT / 'census.json').read_text())['rows']:
    name = entry['function']
    folder = OUT / 'states' / name
    front = json.loads((folder / 'before-frontend.json').read_text())
    if "member reference base type 'void'" not in front['diagnostics']:
        continue
    source = (folder / 'before.c').read_text()
    asm = (Path.home() / 'decomp/experiments/clean-members-20260922/final-builds' / name / 'nonmatchings' / name / 'target.s').read_text()
    report = namespace['propose'](source, name, asm, front['diagnostics'])
    for item in report.get('inspection', []):
        item['function'] = name
        rows.append(item)
(OUT / 'void-declines.json').write_text(json.dumps(rows,indent=2)+'\n')
print('declines', Counter((r['reason'],r['local']) for r in rows))
print('states', Counter(r['function'] for r in rows).most_common(15))
