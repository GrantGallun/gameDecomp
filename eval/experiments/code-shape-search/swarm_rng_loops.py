import json, sqlite3, sys, time, re
from dataclasses import asdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace
out = ROOT / 'eval/results/swarm-rng-loops'
out.mkdir(exist_ok=True)
source = (ROOT / 'eval/results/swarm-rng-v1/0.c').read_text()
start = source.index('    var_v0 = 0;')
end = source.index('    { f32 code_shape_stage;', start)
original = source[start:end]
body = original[original.index('        temp_v1 ='):original.index('    \n        if (!')]
single = body[:body.index('        temp_v1_2 =')]
loops = [('for4top', 'for (var_v0 = 0; var_v0 != 8;) { var_v0 += 4;'+body+'}'),
         ('for4bottom','for (var_v0 = 0; var_v0 != 8; var_v0 += 4) {'+body+'}'),
         ('while4top','var_v0 = 0; while (var_v0 != 8) { var_v0 += 4;'+body+'}'),
         ('for1','for (var_v0 = 0; var_v0 < 8; var_v0++) {'+single+'}'),
         ('for1top','for (var_v0 = 0; var_v0 < 8;) { var_v0++;'+single+'}'),
         ('while1','var_v0 = 0; while (var_v0 < 8) {'+single+'var_v0++; }')]
variants = [(label, source[:start]+loop+'\n'+source[end:]) for label,loop in loops]
conn = sqlite3.connect(ROOT / 'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite')
rows=[]
for i,(label,code) in enumerate(variants):
    tag=f'__MusIntRandom_swarm_rng_loop_{time.time_ns()}'
    a=workspace.score(Path('/home/grant/decomp/sbk1/nonmatchings/__MusIntRandom'),Path('/home/grant/decomp/sbk1'),tag,code,conn=conn,func='__MusIntRandom')
    rows.append(dict(label=label,tag=tag,**asdict(a)))
    (out/f'{i}.c').write_text(code)
    (out/'scores.json').write_text(json.dumps(rows,indent=2))
    print(i,label,a.score,a.exact,flush=True)
