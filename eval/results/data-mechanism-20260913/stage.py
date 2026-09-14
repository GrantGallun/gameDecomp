"""Scoped binary-data and measured small-function release over frozen code."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
LIVE=ROOT/'eval/results/resume-pipeline-20260908/code'
STAGE=OUT/'staged-code'
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
if '--refresh' not in sys.argv:
    shutil.copytree(LIVE,STAGE,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','results'))
    (STAGE/'eval/results').symlink_to(ROOT/'eval/results',target_is_directory=True)
manifest={}
def write(rel,text):
    if rel.endswith('.py'): compile(text,rel,'exec')
    (STAGE/rel).write_text(text)
    manifest[rel]={'old_sha256':sha(LIVE/rel),'new_sha256':sha(STAGE/rel)}
def replace(text,before,after):
    assert text.count(before)==1, before
    return text.replace(before,after,1)
for rel in ('solver/binary_data.py','solver/data_memory.py','solver/byte_test_inline.py',
            'eval/campaign_data.py','eval/data_matching.py','eval/fast_campaign.py','eval/semantic_lane.py',
            'solver/principle_variants.py','solver/hardware_environment.py','solver/function_boundary.py',
            'eval/prepare_integration.py','tests/test_binary_data.py','tests/test_data_memory.py',
            'tests/test_campaign_data.py','tests/test_byte_test_inline.py','tests/test_encoded_register_addresses.py',
            'tests/test_function_boundary.py','tests/test_prepare_integration.py'):
    write(rel,(ROOT/rel).read_text())
rel='eval/agentrepair.py'
s=(LIVE/rel).read_text()
s=replace(s,'        runtime_captures: tuple[dict, ...] = ()) -> dict:',
            '        runtime_captures: tuple[dict, ...] = (), memory_context: dict | None = None) -> dict:')
s=replace(s,"    if runtime_captures:\n        config['runtime_captures']",
            "    if memory_context:\n        config['binary_data_memory_sha256'] = memory_context['sha256']\n    if runtime_captures:\n        config['runtime_captures']")
s=replace(s,'        panel = DeferredPanel(repo,ws,function,semantic_cases,semantic_steps)\n',
            "        panel = DeferredPanel(repo,ws,function,semantic_cases,semantic_steps,\n                              **({'memory_context':memory_context} if memory_context else {}))\n")
write(rel,s)
rel='eval/completion_campaign.py'
s=(LIVE/rel).read_text()
s=replace(s,'    result = agentrepair.run(repo=repo, db=db, function=function, source=source,',
            '    from eval import campaign_data\n    result = agentrepair.run(repo=repo, db=db, function=function, source=source,')
s=replace(s,"        runtime_captures=tuple(config.get('runtime_captures', {}).get(function, [])),\n",
            "        runtime_captures=tuple(config.get('runtime_captures', {}).get(function, [])),\n        memory_context=config.get('binary_data_memory', {}).get(function),\n")
s=replace(s,'            + json.dumps(node.get("last_outcome", {}), sort_keys=True))["result"]',
            '            + json.dumps(node.get("last_outcome", {}), sort_keys=True)\n            + campaign_data.prompt(config, function))["result"]')
write(rel,s)
rel='solver/repair_queue.py'
s=(LIVE/rel).read_text()
anchor="        **({'shared_evidence': node['shared_evidence_sha256']} if node.get('shared_evidence_sha256') else {}),\n"
s=replace(s,anchor,anchor+"        **({'binary_data': node['data_evidence_sha256']} if node.get('data_evidence_sha256') else {}),\n")
write(rel,s)
rel='patterns/catalog.py'
s=(ROOT/rel).read_text()
statement=next(n for n in ast.parse(s).body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call)
               and 'split-byte-zero-test-load' in ast.get_source_segment(s,n))
block=ast.get_source_segment(s,statement)+'\n\n'
old=(LIVE/rel).read_text()
offset=old.index('register(Pattern(')
write(rel,old[:offset]+block+old[offset:])
(OUT/'staged-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
protocol=(ROOT/'eval/results/zero-gain-20260913/deploy.py').read_text()
protocol=protocol.replace("RUNTIME_PREFIXES=('solver/','eval/')", "RUNTIME_PREFIXES=('solver/','eval/','patterns/')")
protocol=protocol.replace('20260913-incumbent-selection','20260913-data-and-small-functions')
protocol=protocol.replace('incumbent-selection-amendment','data-and-small-functions-amendment')
protocol=protocol.replace('User requested fixing repeated zero-gain repairs in the ongoing campaign.',
                         'User requested implementing measured small-function fixes and better code/data matching machinery in the ongoing campaign.')
protocol=protocol.replace('Keep the freshly evaluated incumbent on measured ties; preserve real semantic improvements and exploration alternatives.',
                         'Pinned data context and readonly diagnostic seeds, bounded measured byte-load rewrite, ROM-bound function relocation gate and AST-checked extern preparation. No C data exactness inferred from catalogued bytes.')
(OUT/'deploy.py').write_text(protocol)
validate=(ROOT/'eval/results/zero-gain-20260913/validate.py').read_text().replace('20260913-incumbent-selection','20260913-data-and-small-functions')
(OUT/'validate.py').write_text(validate)
print(json.dumps({'files':len(manifest),'stage':str(STAGE)}))
