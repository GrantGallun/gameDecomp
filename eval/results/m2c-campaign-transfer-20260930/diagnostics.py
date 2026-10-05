"""Post-evaluation descriptions, not adapter fitting or a new heldout claim."""
from pathlib import Path
import collections
import json
import re
import sqlite3

HERE=Path(__file__).resolve().parent
P=HERE/'portable'
comparison=json.loads((P/'comparison.json').read_text())['rows']
conn=sqlite3.connect((P/'attempts.sqlite').as_uri()+'?mode=ro',uri=True)
conn.row_factory=sqlite3.Row
patterns={'call_argument_type':r'incompatible pointer.*passing|passing .*to parameter',
          'undeclared_function':r'implicit declaration of function',
          'member_access':r'member reference|no member named',
          'return_pointer_type':r'incompatible pointer.*returning',
          'address_arithmetic':r'invalid operands to binary|Unacceptable operand',
          'aggregate_conversion':r'operand of type .*struct|aggregate'}
rows=[];counts=collections.Counter()
for row in comparison:
    if row['arm']!='current' or row['usable_seed'] or not row['selected_attempt_id']:continue
    record=conn.execute('SELECT sampling,compiler_stderr,source_sha256 FROM attempts WHERE id=?',
                        (row['selected_attempt_id'],)).fetchone()
    diag=(json.loads(record['sampling']).get('frontend') or {}).get('diagnostics','')
    combined=diag+'\n'+record['compiler_stderr']
    categories=[name for name,pattern in patterns.items() if re.search(pattern,combined)]
    counts.update(categories)
    rows.append({'function':row['function'],'attempt_id':row['selected_attempt_id'],
                 'source_sha256':record['source_sha256'],'categories':categories,
                 'frontend_diagnostics':diag,'compiler_diagnostics':record['compiler_stderr']})
conn.close()
result={'scope':'baseline selected invalid sources; overlapping descriptive regex families, not causal proof',
        'invalid_selected_sources':len(rows),'functions_per_family':dict(counts),
        'patterns':patterns,'rows':rows,'training_eligible':False,'adapter_modified':False}
(HERE/'diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('invalid_selected_sources','functions_per_family','scope')}))
