"""Read-only capability/readiness audit; no preparation, build or database copy."""
import sys
# Neighboring ad-hoc inspect.py must not shadow the standard-library module.
sys.path=[p for p in sys.path if not p.endswith('residual-patterns-20260912')]
from collections import Counter
import hashlib
import json
import sqlite3
from pathlib import Path

from eval.campaign_state import read
from eval import prepare_integration
from solver import function_boundary

root=Path('/mnt/c/Code/gameDecomp')
campaign=root/'eval/results/resume-pipeline-20260908'
pointer=json.loads((campaign/'campaign.json').read_text())
state=read(campaign/'campaign.json')
pending={n:v for n,v in state['nodes'].items() if v['status']=='function_exact_pending_integration'}
with sqlite3.connect(f'file:{campaign / "campaign.sqlite"}?mode=ro',uri=True,timeout=20) as conn:
    metadata={name:conn.execute('SELECT f.name,f.addr,f.size,a.source_code,t.name FROM attempts a '
          'JOIN functions f ON a.func_addr=f.addr JOIN tus t ON f.tu_id=t.id WHERE a.id=?',
          (node['attempt_id'],)).fetchone() for name,node in pending.items()}
report={'checkpoint':pointer.get('commit'), 'campaign_status':state['status'],
        'status_counts':dict(Counter(n['status'] for n in state['nodes'].values())),
        'configuration':state['config'], 'campaign_integration_receipts':state.get('integrations',[]),
        'pending':[],'prior_isolated_rom_receipts':[]}
for name,node in pending.items():
    source_path=Path(node['source'])
    source=source_path.read_text()
    verification=node.get('verification') or {}
    boundary=verification.get('function_boundary') or {}
    row={'function':name,'attempt_id':node.get('attempt_id'),'source':str(source_path),
         'source_sha256':node.get('source_sha256'),'source_hash_matches':hashlib.sha256(source.encode()).hexdigest()==node.get('source_sha256'),
         'instruction_count':node.get('instruction_count'),'size':node.get('size'),
         'frontend':(node.get('residual') or {}).get('frontend'),
         'verification':verification, 'latest_work_receipt':(node.get('jobs') or [{}])[-1].get('receipt')}
    try:
        body,includes=prepare_integration.candidate_parts(source,name)
        row['candidate_parts']={'status':'supported','includes':includes,'body_characters':len(body)}
    except Exception as exc:
        row['candidate_parts']={'status':'declined','error':str(exc)}
    try:
        row['boundary_revalidated']=function_boundary.revalidate(boundary)
    except Exception as exc:
        row['boundary_revalidation_error']=str(exc)
    changed=[]
    for path,digest in verification.get('build_inputs',{}).items():
        file=Path(path)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
            changed.append(path)
    row['changed_build_inputs']=changed
    record=metadata[name]
    row['attempt_lineage_matches']=bool(record and record[0]==name and record[3]==source)
    row['boundary_metadata_matches']=bool(record and tuple(record[1:3])==(boundary.get('address'),boundary.get('size')))
    if record:
        tu=record[4]
        if tu.startswith('build/') and tu.endswith('.o'):
            tu=tu[6:-2]+'.c'
        row['translation_unit']=tu
        try:
            game=Path(state['config']['repo'])
            original=prepare_integration.inside(game,tu).read_text()
            # Reference C is an opaque splice destination only. No contents
            # or generated replacement enter the audit/prompt output.
            prepare_integration.replace_function(original,source,name)
            _,includes=prepare_integration.candidate_parts(source,name)
            missing=[inc for inc in includes if not prepare_integration.inside(game/'include',inc).is_file()]
            row['tu_preflight']={'status':'supported' if not missing else 'missing_headers', 'missing_headers':missing}
        except Exception as exc:
            row['tu_preflight']={'status':'declined','error':str(exc)}
    report['pending'].append(row)
# Restrict search to actual top-level results artifact directories, not frozen
# copies of source/tests. No attempt database access is needed.
for folder in (root/'eval/results').iterdir():
    if not folder.is_dir() or not folder.name.endswith('-artifacts'):
        continue
    for path in folder.glob('*-integration.json'):
        receipt=json.loads(path.read_text())
        if receipt.get('kind')!='isolated-rom-integration':
            continue
        entry={k:receipt.get(k) for k in ('status','whole_rom_verified','target_bytes','candidate_sha256','target_sha256','built_rom_artifact','replacements')}
        entry['receipt']=str(path)
        image=Path(receipt['built_rom_artifact']) if receipt.get('built_rom_artifact') else None
        entry['archived_image_present']=bool(image and image.is_file())
        if image and image.is_file():
            entry['archived_image_hash_matches']=hashlib.sha256(image.read_bytes()).hexdigest()==receipt.get('candidate_sha256')
        manifest=folder/(path.stem.replace('-integration','-prepared'))/'manifest.json'
        if manifest.is_file():
            manifest_data=json.loads(manifest.read_text())
            entry['functions']=[item['function'] for item in manifest_data.get('lineage',[])]
            entry['same_pending_sources']=[item['function'] for item in manifest_data.get('lineage',[])
                if item['function'] in pending and item.get('source_sha256')==pending[item['function']]['source_sha256']]
        report['prior_isolated_rom_receipts'].append(entry)
out=Path(__file__).with_name('integration-audit.json')
out.write_text(json.dumps(report,indent=2))
print(json.dumps({'checkpoint':report['checkpoint'],'statuses':report['status_counts'],
 'pending':[{k:v for k,v in r.items() if k not in ('verification','frontend')} for r in report['pending']],
 'prior_receipts':dict(Counter(r['status'] for r in report['prior_isolated_rom_receipts'])),
 'verified_archived_images':sum(r.get('archived_image_hash_matches',False) for r in report['prior_isolated_rom_receipts'])},indent=2))
