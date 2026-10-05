"""Bounded source-independent applicability census; no compiler search or activation.

Historical artifacts supply exclusion names only. Candidate drafting uses target
assembly, ELF observations and public ABI headers, never reference C bodies.
"""
from pathlib import Path
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
WORK = Path('/home/grant/decomp/experiments/narrow-update-transfer-20261002-bounded')
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def witness(assembly):
    from solver import cfg
    instructions, _ = cfg.parse_assembly(assembly)
    for i, load in enumerate(instructions):
        if load.opcode not in {'lbu', 'lhu'} or len(load.operands) != 2:
            continue
        reg, address = load.operands
        for j in range(i + 1, min(i + 17, len(instructions))):
            inc = instructions[j]
            if inc.opcode != 'addiu' or len(inc.operands) != 3 or inc.operands[1] != reg:
                continue
            try:
                if int(inc.operands[2], 0) != 1:
                    continue
            except ValueError:
                continue
            for store in instructions[j + 1:j + 17]:
                if store.opcode == {'lbu': 'sb', 'lhu': 'sh'}[load.opcode] and store.operands == (inc.operands[0], address):
                    return {'load': load.text, 'increment': inc.text, 'store': store.text,
                            'claim': 'textual candidate selector only; not a dataflow or equivalence proof'}
    return None


def main():
    assert sys.platform == 'linux'
    WORK.mkdir(parents=True, exist_ok=False)
    code = WORK / 'code'
    for folder in ('solver', 'miner', 'kb', 'patterns', 'eval'):
        shutil.copytree(PROJECT / folder, code / folder,
                        ignore=shutil.ignore_patterns('__pycache__', 'results', '*.sqlite', '*.pyc'))
    sys.path.insert(0, str(code))
    from eval import clean_set
    from solver import binary_type_draft, narrow_update
    # Windows owns the historical files. Run the same existing metadata helper
    # there, rather than paying a cross-filesystem traversal cost for every file.
    history_path = HERE / 'history-names.json'
    history = json.loads(history_path.read_text())
    prior_names = set(history['names'])
    shutil.copyfile(history_path, WORK / 'history-names.json')
    pins = {str(p.relative_to(code)): sha(p) for p in code.rglob('*') if p.is_file()}
    protocol = {
        'selection': 'Never-attempted game-source functions, no historical result/set/recovered membership; unsigned-load/add-one/same-address narrow-store textual selector; <=150 instructions; sort by size then name; at most 24 functions per game.',
        'drafts': 'Existing frozen binary_type_draft variants; no reference C or game headers enter construction; first applicable variant per function; no generator tuning.',
        'limits': {'functions_per_game': 24, 'compiler_evaluations': 0, 'model_calls': 0},
        'interpretation': 'Applicability census only. SBK1 uses IDO; SBK2 uses KMC GCC, so compiler transfer is a separate question.',
        'training_eligible': False, 'activation': False,
        'code_pins': pins, 'runner_sha256': sha(__file__),
        'history_names_sha256': sha(history_path), 'history_source': history['source'],
        'historical_artifact_gaps': history['unreadable_or_partial_artifacts'],
        'exposure_scope': history['limits'],
        'comparison_protocol_sha256': sha(HERE / 'comparison-protocol.json'),
    }
    (WORK / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    results = []
    for game in ('sbk1', 'sbk2'):
        repo = Path('/home/grant/decomp') / game
        with sqlite3.connect(f'file:/home/grant/decomp/kb-{game}.sqlite?mode=ro', uri=True) as conn:
            attempted = clean_set._attempted_names(conn)
            rows = conn.execute('SELECT f.name,f.insn_count,t.name FROM functions f JOIN tus t ON t.id=f.tu_id WHERE f.insn_count BETWEEN 8 AND 150 ORDER BY f.insn_count,f.name').fetchall()
        excluded = attempted | prior_names
        eligible = [(name, size, tu) for name, size, tu in rows
                    if name not in excluded and tu.startswith('src/') and
                    not any(word in tu.lower() for word in ('ultra', 'libmus', 'libc', 'audio'))]
        files = {p.stem: p for p in (repo / 'asm').rglob('*.s')}
        selected = []
        for name, size, tu in eligible:
            path = files.get(name)
            if path is None:
                continue
            why = witness(path.read_text())
            if why:
                selected.append({'function': name, 'insn_count': size, 'tu': tu,
                                 'assembly': str(path), 'assembly_sha256': sha(path), 'selector': why})
            if len(selected) == 24:
                break
        record = {'game': game, 'attempted_functions': len(attempted),
                  'metadata_eligible_functions': len(eligible), 'selected': selected,
                  'historical_result_set_recovered_exclusions': len(prior_names),
                  'draft_results': []}
        (WORK / (game + '-selection.json')).write_text(json.dumps(record, indent=2) + '\n')
        print(json.dumps({'game': game, 'eligible': len(eligible), 'selected': len(selected)}), flush=True)
        for root in selected:
            name = root['function']
            folder = WORK / game / name
            folder.mkdir(parents=True)
            assert sha(root['assembly']) == root['assembly_sha256']
            shutil.copyfile(root['assembly'], folder / 'target.s')
            started = time.monotonic()
            candidates, reports = binary_type_draft.variants(repo, name, folder)
            row = {'function': name, 'draft_count': len(candidates), 'reports': reports,
                   'applicable': False, 'seconds': time.monotonic() - started}
            for label, source in candidates:
                proposals = list(narrow_update.variants(source, name))
                if proposals:
                    path = folder / 'root.c'
                    path.write_text(source)
                    row.update(applicable=True, root=str(path), root_sha256=sha(path),
                               draft_label=label, proposals=len(proposals))
                    break
            record['draft_results'].append(row)
            print(json.dumps({'game': game, 'function': name, 'drafts': len(candidates), 'applicable': row['applicable']}), flush=True)
            (HERE / 'census-partial.json').write_text(json.dumps(results + [record], indent=2) + '\n')
        results.append(record)
    result = {'status': 'census_complete', 'games': results, 'compiler_evaluations': 0,
              'model_calls': 0, 'training_eligible': False, 'work': str(WORK),
              'protocol': str(WORK / 'protocol.json'), 'protocol_sha256': sha(WORK / 'protocol.json')}
    (HERE / 'census.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'complete': True, 'applicable': {r['game']: sum(x['applicable'] for x in r['draft_results']) for r in results}}), flush=True)


if __name__ == '__main__':
    main()
