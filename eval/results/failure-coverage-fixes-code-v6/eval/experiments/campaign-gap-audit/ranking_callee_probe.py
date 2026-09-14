"""Isolate interpreter capability from concrete-call admission; no C bodies."""
import hashlib
from pathlib import Path

from eval import agentrepair
from solver import workspace, mips_differential as d


def main():
    root = Path('/mnt/c/Code/gameDecomp')
    function = 'getRacePlayerRankingProgress'
    output = root/'eval/results/ranking-callee-isolated-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite experiment')
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    ws = Path('/home/grant/decomp/sbk1/nonmatchings')/function
    obj = ws/'target.o'
    dump = ws/'target_object_dump_normalized.s'
    assembly = workspace.semantic_assembly(dump.read_text(),obj)
    program = d.Program.parse(function,assembly)
    rows = []
    for course in range(9):
        case = d.TestCase('course'+str(course),54784,
            global_writes=(('gRaceCourseIndex',2,course),),
            entry_registers=(('a0',0),('a1',d.ARG_POINTER_BASES['a1']),
                             ('a2',d.ARG_POINTER_BASES['a2'])))
        run = d.execute_case(program,case,max_steps=10000)
        rows.append({'course':course,'status':run.status,'steps':run.instruction_count,
                     'error':run.error,'execution':run.to_dict()})
    agentrepair._atomic_json(output,{
        'kind':'isolated-callee-interpreter-capability-probe','function':function,
        'bindings':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (obj,dump,Path(__file__),Path(d.__file__))},
        'semantic_assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'instructions':len(program.instructions),'symbols':sorted(program.symbols),
        'data_word_count':len(program.data_words),'rows':rows,
        'reference_bodies_used':False,'model_calls':0,'integration_requested':False,
        'scope':'artifact-bound standalone capability only; synthetic globals/inputs, '
                'no fresh independent ROM certificate or caller/callee integration proof'})
    print([(row['course'],row['status'],row['steps']) for row in rows])


if __name__ == '__main__':
    main()
