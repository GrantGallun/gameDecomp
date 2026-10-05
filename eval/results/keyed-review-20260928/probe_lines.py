from pathlib import Path
import sys,json,subprocess,tempfile,hashlib
root=Path('/mnt/c/Code/gameDecomp'); out=root/'eval/results/keyed-review-20260928'
sys.path.insert(0,str(root/'eval/results/resume-pipeline-20260908/code'))
from solver import ido_stages,compiler_experiment,byte_certificate
repo=Path('/home/grant/decomp/sbk1'); entries=[]
print('ASSERT_HEADER', (repo/'include/assert.h').read_text())
with tempfile.TemporaryDirectory(prefix='key-line-review-',dir='/tmp') as td:
 work=Path(td); ws=work/'ws';ws.mkdir()
 (ws/'.compiler-target.json').write_text(json.dumps({'function':'review_probe','target':'build/src/engine/viewport_manager.o'}))
 command=ido_stages._recipe_command(repo,ws,'review_probe')
 cases={
 'filename_macro':('char *review_probe(void) { return __FILE__; }\n','char *review_probe(void) { return __FILE__; }\n'),
 'line_macro':('int review_probe(void) { return __LINE__; }\n','\n\nint review_probe(void) { return __LINE__; }\n'),
 'assert_ndebug':('#include <assert.h>\nint review_probe(int x) { assert(x); return x; }\n','#include <assert.h>\n\n\nint review_probe(int x) { assert(x); return x; }\n'),
 'assert_enabled':('#undef NDEBUG\n#include <assert.h>\nint review_probe(int x) { assert(x); return x; }\n','#undef NDEBUG\n#include <assert.h>\n\n\nint review_probe(int x) { assert(x); return x; }\n'),
 'line_directive_loop':('#line 10 "probe.c"\nint review_probe(int *p,int n) { int a=0; while(n--) { a+=*p++; } return a; }\n','#line 1000 "probe.c"\nint review_probe(int *p,int n) { int a=0; while(n--) { a+=*p++; } return a; }\n'),
 }
 for name,(a,b) in cases.items():
  records=[]
  for idx,source in enumerate((a,b)):
   d=work/f'{name}-{idx}'; d.mkdir();(d/'raw.c').write_text(compiler_experiment._compile_source(repo,source))
   def run(cmd):
    r=subprocess.run(cmd,cwd=d,text=True,capture_output=True,timeout=60)
    if r.returncode: raise RuntimeError(str(cmd)+'\n'+r.stdout+r.stderr)
    return r
   filename=f'.build-source.REVIEW{idx}.c' if name=='filename_macro' else 'cand.c'
   run([sys.executable,str(repo/'tools/textconv.py'),str(repo/'tools/charmap.txt'),'raw.c',filename])
   pp=run([x if x!='-c' else '-E' for x in command]+[filename]).stdout
   run(command+[filename])
   k=ido_stages.optimizer_key(repo,ws,'review_probe',source)
   records.append({'key':k,'obj':str(d/Path(filename).with_suffix('.o')),'source':source,'preprocessed_tail':pp[-1500:]})
  cert=byte_certificate.certify(Path(records[0]['obj']),Path(records[1]['obj']),source=b)
  row={'case':name,'same_key':records[0]['key']==records[1]['key'],'both_keys':all(r['key'] for r in records),'certificate':cert,'records':records}
  entries.append(row); print(name,row['same_key'],cert.get('exact'),cert.get('status'),flush=True)
 payload={'command':command,'rows':entries,'compiler_sha256':hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()}
 (out/'synthetic-lines.json').write_text(json.dumps(payload,indent=2))
