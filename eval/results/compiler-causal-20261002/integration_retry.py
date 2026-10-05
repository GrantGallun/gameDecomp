"""Private type admission from compiler diagnostics; no reference body read."""
from pathlib import Path
import hashlib
import json

HERE = Path(__file__).resolve().parent
inputs = json.loads((HERE / 'inputs.json').read_text())
folder = Path(inputs['work']) / 'updateEndingCreditsIdleSparkle'
source = (folder / 'materialization-exact.c').read_text()
changes = [
    ('void drawEndingCreditsIdleSparkle(void *);',
     'void drawEndingCreditsIdleSparkle(EndingCreditsEffectActor *);'),
    ('void updateEndingCreditsIdleSparkle(void *arg0)',
     'void updateEndingCreditsIdleSparkle(EndingCreditsEffectActor *arg0)'),
    ('&gMenuRenderCallbackList, drawEndingCreditsIdleSparkle, arg0',
     '&gMenuRenderCallbackList, (RenderCallback)drawEndingCreditsIdleSparkle, arg0'),
]
for before, after in changes:
    assert source.count(before) == 1
    source = source.replace(before, after)
path = folder / 'integration-typed.c'
assert not path.exists()
path.write_text(source)
(HERE / 'integration-revision.json').write_text(json.dumps({
    'function': 'updateEndingCreditsIdleSparkle',
    'source': str(path), 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'evidence': 'Prior failed private full-ROM compiler diagnostics: destination forward declaration and callback parameter type',
    'changes': changes, 'reference_body_used': False,
    'assistance': 'Game headers, destination declarations and unknown earlier candidate ancestry',
    'training_eligible': False, 'imported': False,
}, indent=2) + '\n')
