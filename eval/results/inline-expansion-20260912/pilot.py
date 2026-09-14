"""Synthetic IDO compiler experiment; no original-game source or candidate import."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from solver.inline_expansion import candidates

output = Path('/home/grant/decomp/inline-expansion-20260912') / ('run-' + str(time.time_ns()))
output.mkdir(parents=True, exist_ok=False)
compiler = Path('/home/grant/decomp/sbk1/tools/ido-recomp/linux/cc')
flags = ['-c', '-O2', '-mips1', '-G', '0', '-non_shared', '-fullwarn', '-Xcpluscomm',
         '-nostdinc', '-Wab,-r4300_mul', '-woff', '649,838,712,516']
source = 'int helper(int x, int y) { return (x << 2) ^ y; }\nint f(int a, int b) { return helper(a, b) + 7; }\n'
target = 'int f(int a, int b) { return ((a << 2) ^ b) + 7; }\n'
variant, = candidates(source, 'f')
receipt = {'kind': 'synthetic-helper-expansion-IDO-experiment', 'compiler': str(compiler),
    'compiler_sha256': hashlib.sha256(compiler.read_bytes()).hexdigest(),
    'generator_sha256': hashlib.sha256(Path('solver/inline_expansion.py').read_bytes()).hexdigest(),
    'label': variant.label, 'output': str(output), 'attempts': [],
    'reference_c_used': False, 'campaign_candidate_import': False, 'model_calls': 0}
for name, text in [('explicit_target', target), ('helper_call', source), ('generator_expansion', variant.source)]:
    path = output / (name + '.c')
    obj = output / (name + '.o')
    path.write_text(text)
    command = [str(compiler), *flags, '-o', str(obj), str(path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    row = {'name': name, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
           'command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    if result.returncode == 0:
        dump = subprocess.run(['mips-linux-gnu-objdump', '-dr', '--disassemble=f', str(obj)],
                              capture_output=True, text=True, timeout=10, check=True).stdout
        (output / (name + '.dump')).write_text(dump)
        row['function_words'] = re.findall(r'^\s*[0-9a-f]+:\s+([0-9a-f]{8})\s', dump, re.M)
        row['object_sha256'] = hashlib.sha256(obj.read_bytes()).hexdigest()
    receipt['attempts'].append(row)
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2))
rows = receipt['attempts']
receipt['expanded_matches_explicit_target'] = rows[0].get('function_words') == rows[2].get('function_words')
receipt['helper_call_differs_from_explicit_target'] = rows[0].get('function_words') != rows[1].get('function_words')
assert all(row['exit_code'] == 0 and row.get('function_words') for row in rows)
assert receipt['expanded_matches_explicit_target'] and receipt['helper_call_differs_from_explicit_target']
(output / 'receipt.json').write_text(json.dumps(receipt, indent=2))
artifact = Path('eval/results/inline-expansion-20260912')
(artifact / ('receipt-' + output.name + '.json')).write_text(json.dumps(receipt, indent=2))
print(json.dumps({'output': str(output), 'label': variant.label,
    'expanded_matches_explicit_target': receipt['expanded_matches_explicit_target'],
    'helper_call_differs_from_explicit_target': receipt['helper_call_differs_from_explicit_target'],
    'attempts': [{'name': row['name'], 'exit_code': row['exit_code'], 'words': row['function_words']} for row in rows]}, indent=2))
