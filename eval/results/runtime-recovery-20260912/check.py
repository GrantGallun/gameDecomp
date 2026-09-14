"""Read-only frozen campaign recovery checks; emits a local receipt."""
import json
import sqlite3
import sys
import time
from pathlib import Path

run = Path('/mnt/c/Code/gameDecomp/eval/results/resume-pipeline-20260908')
sys.path.insert(0, str(run / 'code'))
from eval import campaign_state, completion_campaign as campaign

with campaign.campaign_lock(run / 'campaign.lock'):
    pointer = json.loads((run / 'campaign.json').read_text())
    state = campaign_state.read(run / 'campaign.json')
    assert not state.get('inflight') and not state.get('fast_inflight')
    repo = Path('/home/grant/decomp/sbk1')
    pins = campaign._pins(run / 'code', repo)
    pins.update(campaign.frozen_wavefront.file_hashes(
        [Path(p) for p in state['pins'] if Path(p).is_relative_to(repo / 'nonmatchings')]))
    assert pins == state['pins'], 'Frozen inputs changed'
    with sqlite3.connect(f'file:{run / "campaign.sqlite"}?mode=ro', uri=True) as conn:
        inventory = list(conn.execute('SELECT name,addr,size,insn_count FROM functions ORDER BY name'))
    assert campaign.digest(inventory) == state['inventory_sha256']
    digest = campaign.frozen_wavefront.model_digest('http://172.28.32.1:11435', 'gpt-oss:20b')
    assert digest == state['model_digest']
    receipt = dict(checked_at=time.time(), checkpoint=pointer['commit'],
                   pins_verified=len(pins), pin_sha256=campaign.digest(pins),
                   model_digest=digest, inventory_verified=True, no_inflight=True,
                   runtime_options=state.get('runtime_options'), summary=state.get('summary'))
    Path(__file__).with_name('pre-resume.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))
