"""Read-only analysis of the already captured private trace; zero compiles."""
import difflib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path('/mnt/c/Code/gameDecomp')
sys.path.insert(0, str(ROOT))
from solver import regalloc_signature, source_attribution, uopt_attribution, uopt_trace

name = 'drawControllerPakFileDeleteConfirmOptions'
base = Path('/home/grant/decomp/experiments/frontier-run-20260926/range-split-trace')
ws = base / 'repo/nonmatchings' / name
out = ROOT / 'eval/results/frontier-run-20260926/range-split/TRACE.json'
result = json.loads(out.read_text())
texts = {key: (base / f'{key}.txt').read_text() for key in ('ugen', 'level5', 'level6')}
target_dump = (ws / 'target_object_dump_normalized.s').read_text()
candidate_dump = (ws / f'{name}_object_dump_normalized.s').read_text()
attr = uopt_attribution.attribute(candidate_dump, texts['level5'], texts['level6'], texts['ugen'], name)
proc = uopt_trace.join(texts['level5'], texts['level6'])[name]
target = regalloc_signature.parse(uopt_attribution.strip_padding(target_dump))
candidate = regalloc_signature.parse(uopt_attribution.strip_padding(candidate_dump))
matcher = difflib.SequenceMatcher(a=[x.shape() for x in target], b=[x.shape() for x in candidate], autojunk=False)
line_rows = source_attribution.instructions_of(json.loads((ws / f'{name}.source-lines.json').read_text()))
range_uses = {}
for record in result['diagnosis']['ranges']:
    if record['class'] != 'split':
        continue
    lr = record['lr']
    uses = []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op != 'equal':
            continue
        for shift in range(a1-a0):
            ti, ci = a0+shift, b0+shift
            wanted = dict(target[ti].registers())
            seen = dict(candidate[ci].registers())
            for position, actual, use_lr in attr.operands.get(ci, []):
                if use_lr != lr or position not in wanted or position not in seen:
                    continue
                uses.append({'target_index': ti, 'candidate_index': ci, 'address': ci*4,
                             'operand_position': position, 'target_register': wanted[position],
                             'candidate_register': actual, 'target_instruction': target[ti].text,
                             'candidate_instruction': candidate[ci].text,
                             'source_line': line_rows[ci] if ci < len(line_rows) else None})
    r = proc.ranges[lr]
    range_uses[str(lr)] = {'lr': lr, 'node': r.node, 'kind': r.kind, 'offset': r.offset,
                           'color': r.color, 'uses': uses}
result['split_range_uses'] = range_uses
target_text = [insn.text for insn in target]
candidate_text = [insn.text for insn in candidate]
different = [i for i, (wanted, got) in enumerate(zip(target_text, candidate_text)) if wanted != got]
swapped = candidate_text.copy()
swapped[10], swapped[12] = swapped[12], swapped[10]
result['instruction_stream_audit'] = {
    'target_instruction_count': len(target_text),
    'candidate_instruction_count': len(candidate_text),
    'different_indices': different,
    'candidate_10_12_swapped_equals_target': swapped == target_text,
    'branch_index_11_target': target_text[11],
    'branch_index_11_candidate': candidate_text[11],
}
with sqlite3.connect(base / 'attempts.sqlite') as conn:
    receipt_id = result['baseline']['receipt_id']
    edge = conn.execute('SELECT parent_attempt_id, child_attempt_id, relation, action FROM attempt_edges WHERE child_attempt_id=?',
                        (receipt_id,)).fetchone()
    diagnostic = conn.execute('SELECT baseline_attempt_id, source_sha256, status, trace_compiles_max FROM trace_diagnostics WHERE baseline_attempt_id=?',
                              (receipt_id,)).fetchone()
    if edge != (result['node']['attempt_id'], receipt_id, 'diagnostic-recompile', 'compile unchanged retained source'):
        raise RuntimeError(f'baseline lineage mismatch: {edge}')
    if diagnostic != (receipt_id, result['source_sha256'], result['status'], 3):
        raise RuntimeError(f'trace diagnostic mismatch: {diagnostic}')
    result['audit'] = {'lineage_edge': edge, 'diagnostic_row': diagnostic,
                       'native_ancestors_cloned': conn.execute('SELECT count(*) FROM attempts WHERE id!=?', (receipt_id,)).fetchone()[0],
                       'baseline_attempt_count': conn.execute('SELECT count(*) FROM attempts WHERE id=?', (receipt_id,)).fetchone()[0]}
out.write_text(json.dumps(result, indent=2))
print(json.dumps(range_uses, indent=2))
