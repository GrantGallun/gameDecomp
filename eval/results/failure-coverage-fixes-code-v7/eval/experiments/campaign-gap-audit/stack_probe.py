"""Synthetic controls for stack-pointee visibility; does not weaken any gate."""
import hashlib
import json
from pathlib import Path

from eval import agentrepair
from solver import mips_differential as differential


def assembly(offset=0x18, value=7, opaque=True):
    call = 'jal readWord\nnop' if opaque else 'lw v0,0(a0)\nnop'
    return f'''addiu sp,sp,-0x40
sw ra,0x14(sp)
li t0,{value}
sw t0,{offset:#x}(sp)
addiu a0,sp,{offset:#x}
{call}
lw ra,0x14(sp)
addiu sp,sp,0x40
jr ra
nop
'''


def main():
    output = Path('eval/results/stack-pointee-audit-v1.json')
    if output.exists():
        raise ValueError('refusing to overwrite diagnostic receipt')
    rows = []
    for label, offset, value in [('identity', 0x18, 7), ('relocated_equal_buffer', 0x1c, 7),
                                  ('same_address_wrong_buffer', 0x18, 8)]:
        pair = {}
        for opaque in (True, False):
            # Equal explicit opaque results isolate argument/memory checks from
            # the default address-dependent synthetic return hash.
            case = differential.TestCase('stack-contract', 20260905,
                call_returns=(('readWord', 0, 7),))
            result = differential.run_suite(assembly(opaque=opaque), assembly(offset,value,opaque),
                (case,), call_arities={'readWord': 1}, return_registers=('v0',))[0]
            pair['opaque' if opaque else 'concrete_read_inlined'] = {
                'status': result.status, 'reasons': result.reasons,
                'first_divergence': result.first_divergence,
                'target_return': result.target.return_values,
                'candidate_return': result.candidate.return_values}
        rows.append({'case': label, **pair})
    assert [(r['opaque']['status'], r['concrete_read_inlined']['status']) for r in rows] == [
        ('passed','passed'), ('failed','passed'), ('passed','failed')]
    receipt = {'kind':'synthetic-stack-pointee-audit',
        'runner_sha256':hashlib.sha256(Path(differential.__file__).read_bytes()).hexdigest(),
        'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'rows':rows,
        'scope':'read-one-word synthetic callee, opaque versus literal inlined load; no real target repaired',
        'conclusion':'stack address comparison is not a pointee/extent/alias contract; opaque calls hide stack-content differences',
        'production_gate_changed':False}
    agentrepair._atomic_json(output, receipt)
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
