"""Apply the small dashboard display changes without altering its layout."""
from pathlib import Path

page = Path(__file__).resolve().parents[3] / 'eval/progress_app.html'
text = page.read_text(encoding='utf-8')
edits = {
    "function control(d){const b=$('control');if(sending)return;":
    "function control(d){const b=$('control');if(sending)return;"
    "if(d.controls_enabled===false){b.disabled=true;b.dataset.act='';"
    "b.textContent='View only';b.title='This run is managed by the batch runner';return;}",
    '`heartbeat ${duration(d.heartbeat_age)} ago · last save ${duration(d.checkpoint_age)} ago`':
    "(d.mode==='deterministic_frontier'?`last save ${duration(d.checkpoint_age)} ago`:"
    '`heartbeat ${duration(d.heartbeat_age)} ago · last save ${duration(d.checkpoint_age)} ago`)',
}
for before, after in edits.items():
    assert text.count(before) == 1, before
    text = text.replace(before, after)
page.write_text(text, encoding='utf-8')
