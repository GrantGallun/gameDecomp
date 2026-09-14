"""Isolated real-toolchain probe and intake replay for the MIPS III/o32 gate."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

from eval import agentrepair, completion_campaign, frozen_wavefront
from solver import byte_certificate, compiler_recipe


def main():
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=root / 'eval/results/resume-blockers-mips3-v1')
    args = parser.parse_args()
    original = Path('/home/grant/decomp/sbk1')
    live = root / 'eval/results/resume-pipeline-20260908/campaign.json'
    checkpoint = json.loads(live.read_text())
    pins = checkpoint['pins']
    frozen_wavefront.verify_files(pins)
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    baseline = root / 'eval/results/kb-sbk1-rom-ranges-v1.sqlite'
    report = {'kind': 'isolated-mips3-o32-recipe-probe',
              'model_calls': 0, 'integration_requested': False,
              'live_pins_unchanged_before': True, 'rows': []}
    with tempfile.TemporaryDirectory(prefix='resume-mips3-') as temporary:
        repo = Path(temporary) / 'repo'
        repo.mkdir()
        for name in ('tools', 'include', 'src', '.venv', 'Makefile', 'symbol_addrs.txt',
                     'snowboardkids.yaml', 'snowboardkids.z64', 'build',
                     'undefined_syms_auto.txt', 'undefined_syms.txt'):
            if (original / name).exists():
                (repo / name).symlink_to(original / name, target_is_directory=(original / name).is_dir())
        database = Path(temporary) / 'probe.sqlite'
        shutil.copy2(baseline, database)
        with sqlite3.connect(database) as db:
            functions = [r[0] for r in db.execute("select f.name from functions f join tus t on t.id=f.tu_id where t.name='build/src/ultra/libc/ll.o' order by f.addr")]
        for function in functions:
            agentrepair._refuse_frozen_heldout(root / 'eval/sets', function)
        recipe = compiler_recipe.resolve(repo, 'build/src/ultra/libc/ll.o')
        assert recipe['settings']['C_MIPS'] == '-mips3 -32'
        # Compiler-only diagnostic, never presented as a generated game solve.
        source = ('typedef char pointer_is_32[sizeof(void*) == 4 ? 1 : -1];\n'
                  'typedef char long_is_32[sizeof(long) == 4 ? 1 : -1];\n'
                  'typedef char long_long_is_64[sizeof(long long) == 8 ? 1 : -1];\n'
                  'unsigned long long multiply(unsigned long long a, unsigned long long b) { return a*b; }\n')
        src, obj = repo / 'abi.c', repo / 'abi.o'
        src.write_text(source)
        built = subprocess.run([*recipe['command'], '-o', str(obj), str(src)], cwd=repo,
                               capture_output=True, text=True, timeout=60)
        report['abi_probe'] = {'source': source, 'recipe': recipe, 'returncode': built.returncode,
                               'diagnostics': built.stdout + built.stderr}
        if built.returncode:
            raise RuntimeError(report['abi_probe']['diagnostics'])
        report['abi_probe']['object_image'] = byte_certificate.object_image(obj.read_bytes())
        dump = subprocess.check_output(['mips-linux-gnu-objdump', '-dr', str(obj)], text=True)
        report['abi_probe']['disassembly'] = dump
        assert 'dmultu' in dump
        (out / 'abi.o').write_bytes(obj.read_bytes())
        (out / 'abi.c').write_text(source)
        for function in functions:
            original_ws = original / 'nonmatchings' / function
            ws = repo / 'nonmatchings' / function
            ws.mkdir(parents=True)
            copied = {}
            for path in original_ws.iterdir():
                if path.is_file() and (path.suffix == '.py' or path.name.startswith('target')
                        or path.name in {'build.sh', 'base.c', 'prelude.inc', '.diff_algorithm'}):
                    shutil.copy2(path, ws / path.name)
                    copied[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
            row = {'function': function, 'input_hashes': copied,
                   'old_blocker': checkpoint['nodes'].get(function, {}).get('blocker')}
            try:
                result = completion_campaign._intake(repo=repo, db=database, function=function,
                                                      node={}, out=out / (function + '.json'))
                row['result'] = result
                if result.get('source') and Path(result['source']).is_file():
                    saved = out / (function + '.candidate.c')
                    shutil.copy2(result['source'], saved)
                    row['saved_source'] = str(saved)
            except Exception as exc:
                row['error'] = f'{type(exc).__name__}: {exc}'
            report['rows'].append(row)
            (out / 'report.json').write_text(json.dumps(report, indent=2))
            print(json.dumps({'function': function, 'error': row.get('error'),
                              'compiled': row.get('result', {}).get('residual', {}).get('compiled'),
                              'exact': row.get('result', {}).get('exact')}), flush=True)
        shutil.copy2(database, out / 'probe.sqlite')
    frozen_wavefront.verify_files(pins)
    report['live_pins_unchanged_after'] = True
    report['status'] = 'complete'
    (out / 'report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
