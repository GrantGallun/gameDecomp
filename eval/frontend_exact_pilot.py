"""Two explicit DEV hypotheses for object-exact/frontend-failed candidates.

No integration. This is an experiment, not a name-based production rewrite.
"""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sqlite3

from eval import agentrepair
from solver import plateau, repair, workspace

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / 'eval/results/frontend-exact-pilot-20260910'
    out.mkdir(exist_ok=False)
    audit = json.loads((ROOT / 'eval/results/failure-mode-audit-20260910.json').read_text())
    original = Path('/home/grant/decomp/sbk1')
    report = {'status': 'running', 'regime': 'two exposed DEV hypotheses; project-header-assisted',
              'integration_requested': False, 'cases': []}
    for name in ('func_80064414', 'fadeOutAllMusicSequences'):
        agentrepair._refuse_frozen_heldout(ROOT / 'eval/sets', name)
        node = next(n for n in audit['high_score_nonexact'] if n['function'] == name)
        source = Path(node['source']).read_text()
        assert repair._digest(source) == node['source_sha256']
        folder = out / name
        repo = folder / 'repo'
        repo.mkdir(parents=True)
        for item in ('tools', 'include', 'src', 'asm', '.venv', 'Makefile', 'symbol_addrs.txt',
                     'snowboardkids.yaml', 'snowboardkids.z64', 'build',
                     'undefined_syms_auto.txt', 'undefined_syms.txt'):
            path = original / item
            if path.exists():
                (repo / item).symlink_to(path, target_is_directory=path.is_dir())
        ws = repo / 'nonmatchings' / name
        ws.mkdir(parents=True)
        for path in (original / 'nonmatchings' / name).iterdir():
            if path.is_file() and (path.suffix == '.py' or path.name.startswith('target') or
                    path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm'}):
                shutil.copy2(path, ws / path.name)
        db = sqlite3.connect(folder / 'attempts.sqlite')
        baseline = sqlite3.connect((ROOT / 'eval/results/kb-sbk1-rom-ranges-v1.sqlite').as_uri()+'?mode=ro', uri=True)
        baseline.backup(db)
        baseline.close()
        parent = workspace.score(ws, repo, 'parent', source, conn=db, func=name,
                                 strategy='frontend-exact-pilot-parent')
        if name == 'func_80064414':
            old = '(*(s32 *)((u8 *)(arg0) + 0x30)) + 6'
            new = '(u16 *)((' + old + '))'
            hypothesis = 'Project header requires u16*; preserve the byte-address expression before converting.'
        else:
            old = ('extern struct AudioThread *gAudioThread;\n'
                   'void osStopThread(struct AudioThread *thread);\n'
                   'void osStartThread(struct AudioThread *thread);')
            new = ('#include "game/audio/audio_engine_internal.h"\n'
                   'void osStopThread(SchedulerThread *thread);')
            hypothesis = ('Header declares gAudioThread as a SchedulerThread object, not pointer; '
                          'retain address-of and project the thread-pointer call interface.')
        assert source.count(old) == 1
        candidate = source.replace(old, new)
        att = workspace.score(ws, repo, 'candidate', candidate, conn=db, func=name,
                              strategy='frontend-exact-pilot', parent_attempt_id=parent.receipt_id,
                              action=hypothesis)
        db.close()
        (folder / 'original.c').write_text(source)
        (folder / 'candidate.c').write_text(candidate)
        report['cases'].append({'function': name, 'hypothesis': hypothesis,
            'parent': asdict(parent), 'candidate': asdict(att),
            'verified_exact': plateau.verified(repair._State(candidate, att))})
        (out / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({'function': name, 'compiled': att.compiled,
                          'frontend': (att.frontend or {}).get('passed'),
                          'verified_exact': report['cases'][-1]['verified_exact']}), flush=True)
    report['status'] = 'complete'
    (out / 'report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
