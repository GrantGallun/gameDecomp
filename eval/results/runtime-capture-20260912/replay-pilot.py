import hashlib
import json
from pathlib import Path
import sys
import time

from solver import project64_capture as p, runtime_capture as r

root = Path('eval/results/runtime-capture-20260912')
input_dir = root / 'nonzero' if '--nonzero' in sys.argv else root
output = input_dir / ('replay-' + str(time.time_ns()))
output.mkdir(exist_ok=False)
rom = root / 'pilot-rom.z64'
raw = json.loads((input_dir / 'project64-entry-raw.json').read_text())
plan = json.loads((input_dir / 'capture-plan.json').read_text())
(output / 'capture-plan.json').write_text(json.dumps(plan, indent=2))
results = {'raw_file': str(input_dir / 'project64-entry-raw.json'),
           'raw_file_sha256': hashlib.sha256((input_dir / 'project64-entry-raw.json').read_bytes()).hexdigest()}
try:
    record = p.import_export(raw, plan, rom)
    (output / 'capture.json').write_text(json.dumps(record, indent=2))
    asm = '\n'.join(line for line in (root / 'target.s').read_text().splitlines()
                    if line.lstrip().startswith('/*'))
    asm += '\n# MIPS_DIFF_SYMBOL gRelocatableHeapBlockStartAliases 0x801101a0\n'
    wrong = asm.replace('jr         $ra', 'xori       $v0, $v0, 1\n jr         $ra')
    if wrong == asm:
        import re
        wrong = re.sub(r'(jr\s+\$ra)', r'xori $v0, $v0, 1\n \1', asm)
    assert wrong != asm
    (output / 'self.s').write_text(asm)
    (output / 'known-wrong.s').write_text(wrong)
    results['self'] = r.replay(record, rom, asm, asm, repo=Path('/home/grant/decomp/sbk1'))
    results['known_wrong'] = r.replay(record, rom, asm, wrong, repo=Path('/home/grant/decomp/sbk1'))
    results['entry_a0'] = record['registers']['a0']
except Exception as error:
    results['error'] = f'{type(error).__name__}: {error}'
    raise
finally:
    (output / 'replay-results.json').write_text(json.dumps(results, indent=2))
    print(json.dumps({'output': str(output), 'error': results.get('error'),
        'a0': results.get('entry_a0'),
        **{name: {'status': results[name]['comparison']['status'],
                  'target_return': results[name]['comparison']['target']['return_values'],
                  'candidate_return': results[name]['comparison']['candidate']['return_values']}
           for name in ('self', 'known_wrong') if name in results}}, indent=2))
