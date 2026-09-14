"""Prepare only the four pointer-related runtime edits; no live mutations."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[3]
run = root/'eval/results/resume-pipeline-20260908'
stage = Path(__file__).parent/'runtime'
shutil.copytree(run/'code', stage/'code', ignore=shutil.ignore_patterns('__pycache__'))

def replace(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)

files = ['address_units.py', 'compile_recovery.py', 'modelrepair.py', 'repair.py']
manifest = {}
for name in files:
    path = stage/'code/solver'/name
    old = path.read_text()
    main = (root/'solver'/name).read_text()
    new = old
    if name == 'address_units.py':
        block = main[main.index('def numeric_relations('):main.index('def address_only_globals(')]
        new = replace(old, 'def address_only_globals(', block+'def address_only_globals(')
    elif name == 'compile_recovery.py':
        anchor = '    from solver import wide_parameter_repair, frontend_repair\n'
        block = main[main.index(anchor)+len(anchor):main.index('    wide_parameter=')]
        new = replace(old, anchor, anchor+block)
        anchor = "        'authority': 'explicit linker values, address-only draft uses; compiler adjudicates'})\n"
        new = replace(new, anchor, anchor+"    reports.append({'stage': 'parameter-call-byte-units', **pointer_report})\n")
    elif name == 'modelrepair.py':
        anchor = '        from solver import frontend_repair\n'
        block = main[main.index(anchor)+len(anchor):main.index('        format_buffer_report=None')]
        new = replace(old, anchor, anchor+block)
        anchor = "                extra=({'stack_buffer_hypotheses':stack_report} if label.startswith('stack-buffer-') else\n"
        replacement = "                extra=({'parameter_call_byte_units':pointer_report} if label=='parameter-call-byte-units' else\n                       {'stack_buffer_hypotheses':stack_report} if label.startswith('stack-buffer-') else\n"
        new = replace(new, anchor, replacement)
    else:
        new = replace(new, 'def _proposal_tasks(frontier: list[_State])',
                      'def _proposal_tasks(frontier: list[_State], *, pointer_context=None)')
        anchor = '        proposals = rewrites.propose(state.source, state.attempt.diff)\n'
        block = main[main.index(anchor)+len(anchor):main.index('        if "stmtorder" in state.kinds:')]
        new = replace(new, anchor, anchor+block)
        anchor = '    tried = 0\n'
        block = main[main.index(anchor)+len(anchor):main.index('    for depth in range(1, max_depth + 1):')]
        new = replace(new, anchor, anchor+block)
        new = replace(new, '        tasks = _proposal_tasks(current)',
                      '        tasks = _proposal_tasks(current, pointer_context=pointer_context)')
    compile(new, name, 'exec')
    path.write_text(new)
    live = run/'code/solver'/name
    manifest[str(live)] = {'before_sha256': hashlib.sha256(live.read_bytes()).hexdigest(),
                          'after_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'staged': str(path)}
    (stage/(name+'.diff')).write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
        fromfile='before/'+name, tofile='after/'+name)))
shutil.copy2(root/'tests/test_parameter_call_units.py', stage/'code/tests/test_parameter_call_units.py')
(stage/'manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps({'staged': str(stage), 'files': files}))
