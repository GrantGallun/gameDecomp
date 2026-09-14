"""Capture installed m2c representation alternatives; no compilation claim."""
import hashlib
import json
from pathlib import Path
from eval import agentrepair
from solver import m2c_input


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    target=repo/'nonmatchings/getRaceCourseSurfaceHeight/target.s'
    output=root/'eval/results/surface-m2c-representation-v1.json'
    if output.exists():
        raise ValueError('refusing to overwrite representation probe')
    agentrepair._refuse_frozen_heldout(root/'eval/sets','getRaceCourseSurfaceHeight')
    arms={}
    for name,options in [('assembly_only',{}),('valid_syntax',{'valid_syntax':True}),
        ('primitive_pointers',{'valid_syntax':True,'context_headers':('common.h',),
            'pointer_globals':(('s16','gRaceCourseSurfaceCoords'),('u16','gRaceCourseSurfaceFaces'),
                ('u8','gRaceCourseSurfaces'))})]:
        result,meta=m2c_input.draft(repo,target,**options)
        arms[name]={'returncode':result.returncode,'source':result.stdout,'stderr':result.stderr,
            'source_sha256':hashlib.sha256(result.stdout.encode()).hexdigest(),'metadata':meta}
    agentrepair._atomic_json(output,{'kind':'m2c-representation-probe','arms':arms,
        'model_calls':0,'reference_bodies_used':False,'integration_requested':False,
        'scope':'primitive pointer types are assisted hypotheses; no compilation/semantic claim',
        'findings':['valid syntax exposes inferred field types and byte offsets as M2C_FIELD',
            'bare dereferences remain and primitive pointer context does not rescale byte arithmetic',
            'typed context must not be accepted without compiler and differential adjudication']})
    print(json.dumps({name:row['source_sha256'] for name,row in arms.items()}))


if __name__=='__main__':
    main()
