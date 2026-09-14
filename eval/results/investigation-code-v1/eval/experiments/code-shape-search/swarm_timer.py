import json, sys, sqlite3, time, re
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import workspace, timer_alternatives, principle_variants

def main():
    out = ROOT / ('eval/results/' + (sys.argv[1] if len(sys.argv)>1 else 'swarm-timer-v1'))
    out.mkdir(exist_ok=True)
    source = (ROOT / (sys.argv[2] if len(sys.argv)>2 else 'eval/results/last-push-final/calculateRaceTimerDelta.c')).read_text()
    repo = Path('/home/grant/decomp/sbk1')
    ws = repo / 'nonmatchings/calculateRaceTimerDelta'
    conn = sqlite3.connect(ROOT / 'eval/results/direct-source-semantic-cohort-v1/attempts.sqlite')
    variants = [principle_variants.Variant('baseline', source)] + timer_alternatives.candidates(source, 'calculateRaceTimerDelta')
    if len(sys.argv)>3 and sys.argv[3]=='direct':
        for first in ('a2->fraction = (s16)(v1 % 0x6400);\n    v1 /= 0x6400;', 'remainder = v1 % 0x6400;\n    v1 /= 0x6400;\n    a2->fraction = (s16)remainder;'):
            for second in ('a2->seconds = (s8)(v1 % 60);\n    v1 /= 60;', 'sec = v1 % 60;\n    v1 /= 60;\n    a2->seconds = (s8)sec;'):
                code = re.sub(r'    (?:quotient|remainder) = v1.*?(?=    return flag;)', '    '+first+'\n    '+second+'\n    a2->minutes = (s8)(v1 % 99);\n\n', source, flags=re.S)
                for clean in (False, True):
                    if clean:
                        for name in ('quotient','remainder','sec','min'):
                            if len(re.findall(r'\b'+name+r'\b',code))==1:
                                code=re.sub(r'    int '+name+r';\n','',code)
                    variants.append(principle_variants.Variant('direct-fields-'+str(len(variants)),code))
    rows = []
    for i, variant in enumerate(variants):
        name = f'calculateRaceTimerDelta_swarm_timer_{time.time_ns()}'
        att = workspace.score(ws, repo, name, variant.source, conn, 'calculateRaceTimerDelta')
        (out / f'{i}.c').write_text(variant.source)
        (out / f'{i}.diff').write_text(att.diff)
        row = dict(index=i, label=variant.label, score=att.score, compiled=att.compiled, exact=att.exact, attempt=att.receipt_id, artifact=name)
        rows.append(row)
        (out / 'summary.json').write_text(json.dumps(rows, indent=2))
        print(row, flush=True)

if __name__ == '__main__': main()
