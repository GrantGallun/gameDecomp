import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import residual_patterns, campaign_state

out = Path(__file__).resolve().parent / 'selection'
names = ['drawCharacterSelectCoursePreviewFrame', 'osPfsReadWriteFile', 'updateRaceHud']
notes = {
    names[0]: 'Two u16 traversal loops (16 entries, then 2 entries); source raw-byte +2 strides. '
              'Target selector*42-byte base offset versus candidate extra shift demonstrates doubled typed-pointer scaling. '
              'Best bounded target-led experiment despite lower score.',
    names[1]: 'u8 data buffer advances 32 bytes per controller-pak block; countdown and goto backedge. '
              'Clean observed pass, but several early-return branches may limit exercised loop cases.',
    names[2]: 'RacePlayer pointer advances 0x60C bytes in separate 2-player and 4-player loops. '
              'Execution debt remains; type-size assumptions require verification before typed increment.'}
rows = []
for name in names:
    folder = out / 'traversal-pool' / name
    metadata = json.loads((folder / 'metadata.json').read_bytes())
    diff = (folder / 'diff.txt').read_text()
    analysis = residual_patterns.analyse([metadata | {'diff': diff}])
    metadata.update(selection_reason=notes[name], function=name,
                    source_path=str(folder / 'source.c'),
                    diff_families=analysis['patterns']['single_instruction_families'],
                    diff_totals=analysis['totals'])
    campaign_state.atomic(folder / 'metadata.json', metadata)
    rows.append(metadata)
campaign_state.atomic(out / 'pointer-shortlist.json', {'checkpoint': 6136, 'candidates': rows,
    'scope': 'Current selected C only, source/address checked and target pins verified; no reference C or live changes',
    'qualification': 'No genuine pointer traversal found among the 10 size>=1024 score>=90 candidates; expanded score floor to75.'})
print(json.dumps([{k: row[k] for k in ('name','score','size','attempt_id','semantic_status','selection_reason')}
                  for row in rows]))
