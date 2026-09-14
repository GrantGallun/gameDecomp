"""Summarize both saved paired DEV cohorts; never reads the live campaign."""
import json
from pathlib import Path
import sqlite3

root = Path(__file__).resolve().parent
reports = [(root / f'plateau-pilot-20260910-v{i}') for i in (1, 2)]
lines = ['# Plateau exploration: September 10 development experiment', '',
         'Both strategies used the same source, toolchain, maximum depth (6), and',
         'candidate compile cap (48). Baseline/final verification were outside that cap.',
         'The second cohort required at least four available rewrites before selection.',
         'It overlaps the first cohort; these are not twelve independent functions.', '',
         '| Cohort | Function | Original score | Existing beam | Plateau mode | Compiles old/new | New max depth | Frontend |',
         '|---|---|---:|---:|---:|---:|---:|---|']
unique = set()
exact = 0
for folder in reports:
    report = json.loads((folder / 'report.json').read_text())
    assert report['status'] == 'complete'
    for case in report['cases']:
        old, new = case['arms']['baseline'], case['arms']['plateau']
        unique.add(case['function'])
        exact += int(new['exact'])
        db = sqlite3.connect((folder / case['function'] / 'plateau/attempts.sqlite').as_uri() + '?mode=ro', uri=True)
        depth = db.execute("SELECT coalesce(max(iteration),0) FROM attempts WHERE run_id=? AND strategy='repair-plateau-v1'",
                           (f"plateau-pilot-{case['function']}-plateau",)).fetchone()[0]
        db.close()
        gate = 'pass' if (new['frontend'] or {}).get('passed') is True else 'blocked'
        lines.append(f"| {folder.name[-2:]} | {case['function']} | {case['historical_score']} | {old['score']} | {new['score']} | {old['rewritten_compiles']}/{new['rewritten_compiles']} | {depth} | {gate} |")
lines += ['', f'{len(unique)} distinct stalled functions; {exact} exact results in the new mode.', '',
          'Interpretation: exploration and deeper compositions activate, but this pilot',
          'does not demonstrate a matching-rate improvement. High similarity is not proof',
          'of a local maximum: missing rewrites and frontend type errors are separate blockers.',
          'Frontend-blocked cases are excluded from claims about scheduler quality; the',
          'existing beam can spend its budget on those while the new mode declines them.', '',
          '86 focused tests passed. A synthetic equal-budget regression reaches an exact',
          'solution through two worse intermediate candidates that the existing beam misses.',
          'That synthetic result validates scheduling behavior, not game-function recovery.', '',
          'The live campaign was not modified. No model calls, reference function bodies,',
          'source integration, held-out claims, semantic improvement claims, or whole-ROM',
          'verification are involved. Project headers and saved bootstrap drafts are assisted',
          'development context. Each arm has private SQLite attempt/parent receipts and a',
          'freshly compiled champion object and verifier result. See each report.json for logs.', '']
destination = root / 'plateau-pilot-20260910-README.md'
destination.write_text('\n'.join(lines))
print(destination)
