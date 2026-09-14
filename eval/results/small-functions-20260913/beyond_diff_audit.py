"""Read-only, source/pin-bound evidence extraction for a fixed inventory."""
import hashlib,json,collections,os
from pathlib import Path
OUT=Path(__file__).resolve().parent
D=json.loads((OUT/'inventory.json').read_text())
rows=[r for r in D['rows'] if r['status'] not in {'object_exact','integrated'}]
weak=[r for r in rows if r['cluster_functions']<=3]
tiny=[r for r in rows if r['size']<=128 and not r['callers'] and not r['callees']]
def local(p):
 if os.name != 'nt': return Path(p)
 if p.startswith('/mnt/c/'): return Path('C:/'+p[7:])
 if p.startswith('/home/'): return Path('//wsl.localhost/Ubuntu'+p)
 return Path(p)
def sha(b):return hashlib.sha256(b).hexdigest()
def thin(r):
 s=r.get('semantic_validation') or {}; q=r.get('residual') or {}
 return {k:r.get(k) for k in ['function','size','score','status','cluster_functions','callers','callees','attempt_id','source_sha256']} | {'semantic_status':s.get('status'),'semantic_source_bound':bool(s and s.get('source_sha256')==r.get('source_sha256')),'semantic_reason':s.get('reason'),'counts':s.get('counts'),'debt':s.get('debt'),'compiler_error':q.get('compiler_error_signature'),'compiled':q.get('compiled'),'faults':q.get('faults'),'frontend':q.get('frontend'),'first_difference':q.get('first_difference'),'last_receipt':(r.get('jobs') or [{}])[-1].get('receipt')}
report={'checkpoint':D['checkpoint'],'inventory_sha256':sha((OUT/'inventory.json').read_bytes()),'weak':[thin(r) for r in weak],'tiny_noedges':[thin(r) for r in tiny]}
names=['osAiGetLength','__osSpSetStatus','sprintf','alSavePull','__osContGetInitData','ldiv','osCreateMesgQueue','initMainMenuSceneModelRenderer','__ll_rshift','__osContDataCrc','alResampleParam','getRacePlayerPathOffset','Fcutoff','getRaceCourseNextSurface','memcpy','strlen']
evidence=[]
for r in rows:
 if r['function'] not in names:continue
 name=r['function']; dest=OUT/'beyond-diff-inputs'/name; dest.mkdir(parents=True,exist_ok=True)
 record=thin(r)
 for kind,path,expected in [('candidate.c',r.get('source'),r.get('source_sha256')),('target.s','/home/grant/decomp/sbk1/nonmatchings/'+name+'/target.s',D['pins'].get('/home/grant/decomp/sbk1/nonmatchings/'+name+'/target.s'))]:
  if not path or not expected:record[kind]={'available':False,'reason':'no source/pinned workspace'};continue
  try: b=local(path).read_bytes()
  except OSError as e:record[kind]={'available':False,'reason':str(e)};continue
  actual=sha(b); record[kind]={'path':path,'sha256':actual,'verified':actual==expected}
  if actual==expected:(dest/kind).write_bytes(b)
 (dest/'metadata.json').write_text(json.dumps(record,indent=2));evidence.append(record)
report['extracted']=evidence
(OUT/'beyond-diff-evidence.json').write_text(json.dumps(report,indent=2))
print(json.dumps({'weak':len(weak),'tiny_noedge':len(tiny),'extracted':[(r['function'],r.get('target.s'),r.get('candidate.c')) for r in evidence]},indent=2))

