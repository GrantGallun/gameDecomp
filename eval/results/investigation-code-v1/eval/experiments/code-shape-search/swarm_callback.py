"""Bounded callback source experiments; never integrates production sources."""
import json
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace

def main():
    out = ROOT / ('eval/results/swarm-callback-v2' if '--focused' in sys.argv else 'eval/results/swarm-callback-v1')
    out.mkdir(exist_ok=True)
    repo = Path('/home/grant/decomp/sbk1')
    function = 'createCallbackTaskPreservingArgs'
    source = (ROOT / f'eval/results/last-push-final/{function}.c').read_text()
    ws = workspace.bootstrap(repo, function)
    conn = sqlite3.connect(ROOT / 'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite', timeout=120)
    options = [('baseline', source)]
    try:
        from solver.callback_alternatives import candidates
        options += [(v.label, v.source) for v in candidates(source, function, 79)]
    except ImportError:
        pass
    if '--focused' in sys.argv:
        import re
        loop=re.search(r'cur = insertAfter->next;\s*while \(cur != NULL\) \{.*?cur = cur->next;\s*\}',source,re.S)
        guarded='''if (insertAfter->next != NULL) {
        cur = gCallbackTaskActiveListSentinel.next;
        for (;;) {
            if (cur->priority < priority) break;
            insertAfter = cur;
            cur = cur->next;
            if (cur == NULL) break;
        }
    }'''
        shapes=[('original',source),('guarded-bottom',source[:loop.start()]+guarded+source[loop.end():])]
        options=[]
        for label,code in shapes:
            for typ in ['u32','u16']:
                base=code.replace('u32 type',typ+' type')
                for mask,statement in [('mask','type &= 0xFFFF;'),('cast','type = (u16)type;'),('nomask',''),('volatile','*(volatile '+typ+' *)&type = type & 0xFFFF;'),('address','*(&type) = type & 0xFFFF;')]:
                    options.append((label+':'+typ+':'+mask,base.replace('type &= 0xFFFF;',statement)))
    rows=[]
    for index, (label, code) in enumerate(options):
        tag = f'{function}_swarm_{time.time_ns()}'
        attempt=workspace.score(ws,repo,tag,code,conn=conn,func=function,
            strategy='swarm-callback',model='',action=label,run_kind='swarm-callback')
        row={'label':label,'attempt':asdict(attempt)}
        rows.append(row)
        (out/f'{index:02d}.c').write_text(code)
        (out/'scores.json').write_text(json.dumps(rows,indent=2))
        print(index,label,attempt.score,attempt.exact,flush=True)

if __name__=='__main__': main()
