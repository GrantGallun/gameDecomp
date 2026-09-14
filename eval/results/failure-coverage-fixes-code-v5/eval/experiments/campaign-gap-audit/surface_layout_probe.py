"""Assisted binary access/stride reconstruction; not an automatic layout solver."""
import hashlib
import importlib
import json
from pathlib import Path

from eval import agentrepair


def main():
    root=Path('/mnt/c/Code/gameDecomp')
    repo=Path('/home/grant/decomp/sbk1')
    function='getRaceCourseSurfaceHeight'
    parent=root/f'eval/results/failure-coverage-fixes-replay-v4-artifacts/{function}.json'
    previous=json.loads(parent.read_text())
    source=Path(previous['result']['best_source_path']).read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest()!=previous['result']['best_source_sha256']:
        raise ValueError('source identity changed')
    output=root/'eval/results/surface-pointer-layout-v1.json'
    if output.exists() or output.with_name(output.stem+'-inputs.json').exists():
        raise ValueError('refusing to overwrite probe')
    definitions='''struct RaceCourseSurface {
    u8 unknown00[0x14];
    u16 faceStartIndex;
    u16 faceEndIndex;
    u8 unknown18[4];
};
struct RaceCourseSurfaceFace {
    u16 unk0;
    u16 unk2;
    u16 unk4;
    u8 unknown6;
    u8 unk7;
};
'''
    replacements=[
        ('extern struct RaceCourseSurface gRaceCourseSurfaces[];',definitions+'extern struct RaceCourseSurface *gRaceCourseSurfaces;'),
        ('extern struct RaceCourseSurfaceFace gRaceCourseSurfaceFaces[];','extern struct RaceCourseSurfaceFace *gRaceCourseSurfaceFaces;'),
        ('extern Vec3s gRaceCourseSurfaceCoords[];','extern Vec3s *gRaceCourseSurfaceCoords;'),
        ('struct RaceCourseSurfaceFace *var_s1;\n\nstruct RaceCourseSurfaceFace *var_s1;','struct RaceCourseSurfaceFace *var_s1;'),
        ('    temp_v0 = &gRaceCourseSurfaces[surfaceIndex];','    sp44 = surfaceIndex * 0x1C;\n    temp_v0 = &gRaceCourseSurfaces[surfaceIndex];'),
        ('gRaceCourseSurfaceCoords[*(gRaceCourseSurfaceFaces + var_s5)]','gRaceCourseSurfaceCoords[((struct RaceCourseSurfaceFace *)((u8 *)gRaceCourseSurfaceFaces + var_s5))->unk0]'),
        ('gRaceCourseSurfaceFaces + var_s5','((struct RaceCourseSurfaceFace *)((u8 *)gRaceCourseSurfaceFaces + var_s5))'),
        ('(gRaceCourseSurfaces + sp44)->unk16','((struct RaceCourseSurface *)((u8 *)gRaceCourseSurfaces + sp44))->faceEndIndex')]
    candidate=source
    for before,after in replacements:
        if before not in candidate:
            raise ValueError('expected source span missing: '+before)
        candidate=candidate.replace(before,after)
    agentrepair._refuse_frozen_heldout(root/'eval/sets',function)
    agentrepair._atomic_json(output.with_name(output.stem+'-inputs.json'),{
        'kind':'assisted-surface-pointer-layout-probe','parent_receipt':str(parent),
        'parent_sha256':hashlib.sha256(parent.read_bytes()).hexdigest(),
        'parent_source_sha256':previous['result']['best_source_sha256'],
        'candidate_sha256':hashlib.sha256(candidate.encode()).hexdigest(),'replacements':replacements,
        'hypotheses':['lw globals provide pointer slots, not inline arrays',
            'surface stride28 and lhu offsets20/22; face stride8 with lhu0/2/4 and lbu7',
            'unaccessed record bytes retained as unknown padding, not named fields',
            'restore source-lost byte index initialization and first-face-coordinate load'],
        'scope':'controlled source-specific probe; unsigned-helper result reconstruction deliberately not included',
        'reference_bodies_used':False,'integration_requested':False})
    provider=importlib.import_module('eval.experiments.campaign-gap-audit.replay_fresh_fixes_v1').NoModel()
    result=agentrepair.run(repo=repo,db=root/'eval/results/kb-sbk1-range-replay-v1.sqlite',
        function=function,source=candidate,source_parent_attempt_id=None,out=output,
        best_source_out=output.with_suffix('.best.c'),model='zero-model-assisted',endpoint='http://127.0.0.1:1',
        draws=1,depth=1,beam=3,max_calls=0,timeout=1,think='low',num_thread=1,temperature=0,num_predict=1,
        seed=20260906,cache_dir=None,verbose=False,provider=provider,resilient=True,
        semantic_cases=64,semantic_steps=10000)['result']
    print(json.dumps({'attempt':result['best_attempt_id'],'exact':result['exact'],
        'compiled':result['best_residual']['compiled'],'score':result['best_residual']['weighted_progress_score'],
        'semantic_status':(result.get('semantic_validation') or {}).get('status'),
        'counts':(result.get('semantic_validation') or {}).get('counts')}))


if __name__=='__main__':
    main()
