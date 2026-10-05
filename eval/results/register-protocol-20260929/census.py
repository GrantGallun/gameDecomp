"""Register protocol over the round-2 register-only handoffs (eval/results/site-edits-20260929).

    cd /mnt/c/Code/gameDecomp && ~/decomp/sbk1/.venv/bin/python eval/results/register-protocol-20260929/census.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import register_protocol, uopt_diagnosis, workspace  # noqa: E402

REPO = Path('/home/grant/decomp/sbk1')
TRACE_CC = Path('/home/grant/decomp/tools-src/ido-trace/cc')
RESULTS = Path('/home/grant/decomp/runs/site-edits-20260929/results-r2.jsonl')
OUT = Path('/home/grant/decomp/runs/register-protocol-20260929')


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in RESULTS.read_text().splitlines()]
    frame = [r for r in rows if r.get('handoff')]
    summary = []
    for row in frame:
        function = row['function']
        ws = workspace.bootstrap(REPO, function)
        tag = f'{function}_regproto_{time.time_ns()}'
        attempt = workspace.score(ws, REPO, tag, row['source'])
        keep = OUT / function
        keep.mkdir(exist_ok=True)
        result = {'function': function, 'handoff_exact': bool(row['handoff'].get('exact'))}
        try:
            candidate = (ws / f'{tag}_object_dump_normalized.s').read_text()
            target = (ws / 'target_object_dump_normalized.s').read_text()
            texts = uopt_diagnosis.traced_compile(ws, REPO, (ws / f'{tag}.c').read_text(), TRACE_CC, function)
            if texts is None:
                result.update(verdict='declined', reason='traced compile failed')
            else:
                for name, text in (('target.s', target), ('candidate.s', candidate), ('level5.txt', texts['level5']),
                                   ('level6.txt', texts['level6']), ('ugen.txt', texts['ugen'])):
                    (keep / name).write_text(text)
                result.update(register_protocol.analyse(target, candidate, texts['level5'], texts['level6'],
                                                        texts['ugen'], function))
        except Exception as exc:        # a harness failure is recorded, not swallowed
            result.update(verdict='error', reason=repr(exc)[:300])
        (keep / 'protocol.json').write_text(json.dumps(result, indent=1))
        summary.append(result)
        levers = '; '.join(f"lr{r['lr']} {r['class']} {r['actual']}->{r.get('desired')} {r['verdict']} "
                           f"[{', '.join(l['lever'] + ':' + l['status'] for l in r['levers'])}]"
                           for r in result.get('ranges', []))
        print(f"{function:48s} {result['verdict']:14s} handoff_exact={result['handoff_exact']} "
              f"{result.get('reason', '')} {levers}", flush=True)
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
