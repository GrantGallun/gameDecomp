"""Real candidate-only attribution on a private workspace and private ledger."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import argparse

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--project', type=Path, default=ROOT)
parser.add_argument('--work', type=Path, default=Path('/home/grant/decomp/experiments/campaign-localization-20261004/fire2'))
parser.add_argument('--output', default='fire.json')
args = parser.parse_args()
sys.path.insert(0, str(args.project))
from solver import compiler_localization, modelrepair, refine, workspace

HERE = Path(__file__).parent
WORK = args.work
WORK.mkdir(parents=True, exist_ok=True)
selected = json.loads((HERE / 'candidates.json').read_text())[0]
function, node = selected['function'], selected['node']
repo = Path('/home/grant/decomp/sbk1')
original = workspace.bootstrap(repo, function)
ws = WORK / function
if ws.exists():
    raise RuntimeError('use a fresh canary workspace; receipts must not be overwritten')
shutil.copytree(original, ws)
# The existing helper derives PROJECT_ROOT from its location under nonmatchings/.
# Preserve that exact root when relocating this private diagnostic workspace.
helper = ws / 'build.sh'
text = helper.read_text()
anchor = 'PROJECT_ROOT="$(cd "$SCRIPT_PATH/../.." && pwd)"'
if text.count(anchor) != 1:
    raise RuntimeError('unrecognized helper project-root derivation')
helper.write_text(text.replace(anchor, 'PROJECT_ROOT="' + str(repo) + '"'))
db = WORK / 'attempts.sqlite'
conn = sqlite3.connect(db)
campaign = sqlite3.connect('file:/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite?mode=ro', uri=True)
for table in ('functions', 'tus'):
    schema = campaign.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?', (table,)).fetchone()
    if schema is None:
        continue
    conn.execute(schema[0])
    if table == 'functions':
        rows = campaign.execute('SELECT * FROM functions WHERE name=?', (function,)).fetchall()
    else:
        rows = campaign.execute('SELECT * FROM tus').fetchall()
    for row in rows:
        conn.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in row) + ')', row)
refine.ensure_schema(conn)
source = Path(node['source']).read_text()
if hashlib.sha256(source.encode()).hexdigest() != node['source_sha256']:
    raise RuntimeError('campaign source changed')
root = workspace.score(ws, repo, 'canary_parent', source, conn=conn, func=function,
    strategy='campaign-localization-canary:header-assisted-campaign-seed', run_id='localization-fire',
    extra={'training_eligible': False, 'origin_campaign_attempt_id': node['attempt_id']})
parent = modelrepair.CandidateState(source, root, ws / 'canary_parent.o')
packet, children = compiler_localization.measure(repo, ws, function, parent, conn=conn,
    run_id='localization-fire', run_config={'purpose': 'integration proof, not a held-out capability result'})
prompt = modelrepair.build_prompt(workspace.target_asm(ws, function), source, root,
                                  localization=packet)
(WORK / 'prompt.txt').write_text(prompt)
class CaptureProvider:
    provider_id = 'localization-wiring-proof-no-model'
    prompts = []
    def generate(self, request):
        self.prompts.append(request.prompt)
        return '{}', {'done_reason': 'stop'}

provider = CaptureProvider()
search = modelrepair.search(repo, function, source, ws, conn=conn,
    model='wiring-proof', endpoint='unused', base_attempt=root, base_object_path=parent.object_path,
    parent_attempt_id=root.receipt_id, draws=1, max_depth=1, max_calls=1, provider=provider,
    run_id='localization-search-fire', compiler_localization=True)
search_localized = bool(provider.prompts and 'COMPILER LOCALIZATION (current candidate' in provider.prompts[0])
rows = list(conn.execute('SELECT id,parent_attempt_id,strategy,compiled,exact FROM attempts ORDER BY id'))
receipt = {'kind': 'real-compiler-localization-fire', 'function': function,
           'source_sha256': node['source_sha256'], 'campaign_attempt_id': node['attempt_id'],
           'assistance': 'inherited existing campaign seed; not clean capability evidence',
           'root_compiled': root.compiled, 'root_exact': root.exact,
           'root_stderr': root.compiler_stderr,
           'attribution_status': (root.source_attribution or {}).get('status'),
           'packet': packet, 'prompt_has_localization': 'COMPILER LOCALIZATION (current candidate' in prompt,
           'search_prompt_has_localization': search_localized,
           'probe_count': len(packet.get('receipt_ids', [])), 'logged_attempts': len(rows),
           'all_probes_parented': all(row[1] == root.receipt_id for row in rows[1:]),
           'certified_probe_children': len(children), 'private_db': str(db), 'private_workspace': str(ws)}
manifest_file = ROOT / 'eval/results/resume-pipeline-20260908/revisions/20261004-compiler-localization/stage.json'
if manifest_file.exists():
    manifest = json.loads(manifest_file.read_bytes())
    receipt['manifest_sha256'] = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
    receipt['payload_hashes'] = {rel: hashlib.sha256((args.project / rel).read_bytes()).hexdigest()
                                 for rel in manifest['changed']}
(HERE / args.output).write_text(json.dumps(receipt, indent=2))
print(json.dumps({k: v for k, v in receipt.items() if k not in {'packet'}}, indent=2), flush=True)
if not (root.compiled and packet['status'] == 'measured' and receipt['probe_count']
        and receipt['prompt_has_localization'] and receipt['all_probes_parented'] and search_localized):
    raise SystemExit(1)
