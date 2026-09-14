"""Target-derived cases for this assisted DEV experiment, not universal proof.

Run with: python3 -m eval.experiments.alLoadParam-hints.audit ID OUTPUT
The interpreter still treats alCopy as opaque; call arguments and pre-call memory
are compared, not its implementation. v0 is compared even though legacy C leaves
it unspecified. Do not promote these results to authoritative semantics.
"""
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from eval import agentrepair
from solver import mips_differential as diff, residual, workspace


def cases():
    table = diff.ARG_POINTER_BASES['a2']
    loop = table + 0x100
    book = table + 0x200
    for seed in (0, 1, 7, 73):
        for param in (0, 4, 5, 6):
            for wave_type in (0, 1, 2):
                for loop_present in (False, True):
                    for length in (0, 8, 9, 19, 0x7fffffff, 0xfffffff7):
                        writes = (
                            ('@arg2', 4, table + 0x400),
                            ('@arg2+0x4', 4, length),
                            ('@arg2+0x8', 1, wave_type),
                            ('@arg2+0xc', 4, loop if loop_present else 0),
                            ('@arg2+0x10', 4, book),
                            ('@arg2+0x100', 4, 3),
                            ('@arg2+0x104', 4, 123),
                            ('@arg2+0x108', 4, 0xffffffff if seed & 1 else 0),
                            ('@arg2+0x200', 4, 2),
                            ('@arg2+0x204', 4, 4),
                        )
                        yield diff.TestCase(
                            f's{seed}-p{param}-t{wave_type}-loop{int(loop_present)}-len{length}',
                            seed, player_writes=((0x28,4,table),(0x18,4,table+0x300)),
                            global_writes=writes, entry_registers=(('a1',param),('a2',table)))
        yield diff.TestCase(f's{seed}-reset-null-table', seed,
                            player_writes=((0x28,4,0),), entry_registers=(('a1',4),))


def main():
    attempt_id, output = int(sys.argv[1]), Path(sys.argv[2])
    if output.exists():
        raise ValueError('receipt already exists')
    repo = Path('/home/grant/decomp/sbk1')
    with sqlite3.connect('/home/grant/decomp/kb-sbk1.sqlite') as conn:
        source = agentrepair._source_for_attempt(conn, attempt_id, 'alLoadParam')
        ws = workspace.bootstrap(repo, 'alLoadParam')
        tag = 'alLoadParam_assisted_audit_' + str(time.time_ns())
        attempt = workspace.score(ws, repo, tag, source, conn=conn, func='alLoadParam',
            strategy='assisted-hint-audit', model='zero-model',
            parent_attempt_id=attempt_id, run_id=tag, relation='fresh-audit',
            action='target-derived cases; v0 retained; opaque alCopy',
            run_kind='assisted-development')
    if not attempt.compiled:
        raise ValueError('candidate does not compile: '+attempt.compiler_stderr)
    target = diff.Program.parse('target', workspace.semantic_assembly(
        (ws/'target_object_dump_normalized.s').read_text(), ws/'target.o'))
    candidate = diff.Program.parse('candidate', workspace.semantic_assembly(
        (ws/(tag+'_object_dump_normalized.s')).read_text(), ws/(tag+'.o')))
    inputs = tuple(cases())
    runs = [diff.compare_programs(target, candidate, case,
            call_arities={'alCopy':3}, return_registers=('v0',), max_steps=2000)
            for case in inputs]
    result = {
        'kind':'assisted-development-target-derived-audit',
        'source_attempt_id':attempt_id, 'audit_attempt_id':attempt.receipt_id,
        'reference_body_used':False,
        'semantic_authoritative':False,
        'limitations':['finite concrete inputs', 'alCopy implementation opaque',
                       'header and diagnostic-metadata assisted',
                       'legacy C return is unspecified; binary v0 still compared'],
        'residual':residual.build(attempt, target_asm=workspace.target_asm(ws,'alLoadParam'),
            target_object=ws/'target.o', candidate_object=ws/(tag+'.o')).to_dict(),
        'counts':dict(Counter(run.status for run in runs)),
        'reason_counts':dict(Counter(reason for run in runs for reason in run.reasons)),
        'final_memory_equal':sum(run.target.persistent_state == run.candidate.persistent_state for run in runs),
        'return_registers_equal':sum(run.target.return_values == run.candidate.return_values for run in runs),
        'target_coverage':diff.coverage_report(target,[run.target for run in runs]).to_dict(),
        'candidate_coverage':diff.coverage_report(candidate,[run.candidate for run in runs]).to_dict(),
        'cases':[asdict(case) for case in inputs],
        'failures':[run.to_dict() for run in runs if run.status != 'passed'][:16],
        'failure_count':sum(run.status != 'passed' for run in runs),
    }
    agentrepair._atomic_json(output,result)
    print(json.dumps({k:result[k] for k in ('counts','target_coverage','candidate_coverage')},indent=2))


if __name__ == '__main__':
    main()
