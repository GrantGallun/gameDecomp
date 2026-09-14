"""Regenerate the real rejected request with the actual prompt builder, then one local model call."""
import copy
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

from eval.fast_runtime import model_lease
from solver import compile_obligations, llm, modelrepair, prompt_budget

directory=Path(__file__).parent
row=json.loads((directory/'prompt-3866.json').read_text())
original=row['prompt_context']
sampling=json.loads(row['sampling'])
base_start=original.index('SEMANTIC REPAIR OBJECTIVE:')
base_end=original.index('\nDETERMINISTIC COMPILE OBLIGATIONS:')
base=original[base_start:base_end]
packet=json.loads(base.split('FAILING OBSERVABLES AND EXECUTED VALUE TREES:\n',1)[1].split('\nCURRENT C:\n',1)[0])
abi=json.loads(base.split('PUBLIC ABI (read-only): ',1)[1].split('\nFAILING OBSERVABLES',1)[0])
source=base.split('\nCURRENT C:\n',1)[1].split('\nACTUAL INCLUDED TYPE DEFINITIONS',1)[0]
headers=json.loads(base.split('ACTUAL INCLUDED TYPE DEFINITIONS (header-assisted, read-only):\n',1)[1].split('\nTARGET INSTRUCTIONS',1)[0])
assembly=base.split('TARGET INSTRUCTIONS (read-only; byte polish is secondary while behavior fails):\n',1)[1].split('\nRECENT VERIFIED REJECTIONS/OUTCOMES:\n',1)[0]
rejections=base.split('\nRECENT VERIFIED REJECTIONS/OUTCOMES:\n',1)[1]
report={**packet,'feedback':[packet['primary_counterexample'],*packet.get('other_failure_classes',[])]}
state=SimpleNamespace(source=source,semantic=report)
captured=[]
def identity(value, **kw):
    captured.append(copy.deepcopy(value))
    return value
# Replay the exact recorded header assistance rather than probing newer files.
# Both packet construction and fallback selection execute the real function.
with patch.object(compile_obligations,'header_types',return_value=headers):
    with patch.object(prompt_budget,'semantic_packet',side_effect=identity):
        modelrepair.semantic_prompt(Path('/home/grant/decomp/sbk1'),'initTrainingCourseRace',state,
                                    assembly,abi,[rejections] if rejections else [])
    assert all(value==packet for value in captured), 'cannot reconstruct the exact raw packet'
    rebuilt=modelrepair.semantic_prompt(Path('/home/grant/decomp/sbk1'),'initTrainingCourseRace',state,
                                       assembly,abi,[rejections] if rejections else [])
prompt=original[:base_start]+rebuilt+original[base_end:]
assert '\nCURRENT C:\n'+source+'\nACTUAL INCLUDED' in prompt
assert assembly in prompt
budget=prompt_budget.context_budget(prompt,6000,num_ctx=32768,response_schema=modelrepair.EDIT_SCHEMA)
(directory/'canary-3866-prompt.txt').write_text(prompt)
report={'original_proposal_id':row['id'],'original_prompt_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'exact_original_raw_packet_reconstructed':True,
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'assembly_sha256':hashlib.sha256(assembly.encode()).hexdigest(),
        'suffix_sha256':hashlib.sha256(original[base_end:].encode()).hexdigest(),
        'context_budget':budget,'old_context_budget':sampling['context_budget'],
        'authority':'one isolated prompt transport canary; no candidate source applied or imported'}
started=time.perf_counter()
with model_lease(Path('/home/grant/decomp/campaign-workers-20260911/model.lock')):
    text,metadata=llm.generate('http://172.28.32.1:11435','gpt-oss:20b',prompt,
        timeout=240,num_predict=6000,num_ctx=32768,think='low',temperature=sampling['temperature'],
        seed=sampling['seed'],response_schema=modelrepair.EDIT_SCHEMA)
report.update(seconds=time.perf_counter()-started,raw_response=text,metadata=metadata)
(directory/'prompt-compaction-canary.json').write_text(json.dumps(report,indent=2))
print(json.dumps({k:v for k,v in report.items() if k not in ('metadata','raw_response')},indent=2))
print(json.dumps({'prompt_eval_count':metadata.get('prompt_eval_count'),'eval_count':metadata.get('eval_count'),
                  'done_reason':metadata.get('done_reason'),'response_characters':len(text)}))
