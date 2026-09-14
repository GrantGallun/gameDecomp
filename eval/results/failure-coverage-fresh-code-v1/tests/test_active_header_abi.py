import json
from types import SimpleNamespace

import pytest


def test_unresolved_callee_probe_is_debt_not_inactive_header_authority(tmp_path,monkeypatch):
    from eval import dag_pipeline_pilot as dag
    def info(repo,name,*args,**kwargs):
        if name=='format':
            raise ValueError('unsupported variadic ABI')
        return {'arity':2,'return_registers':['v0'],'issues':[]}
    monkeypatch.setattr(dag,'prototype_info',info)
    arities,contracts=dag._call_contracts(tmp_path,['format','ordinary'],{'source':'headers'})
    assert arities['ordinary']==2 and contracts['ordinary']['arity_known']
    assert arities['format']==4  # Existing diagnostic fallback, never ABI evidence.
    assert not contracts['format']['arity_known']
    assert contracts['format']['argument_words'] is None
    assert contracts['format']['issues']==['format: unsupported variadic ABI']

from solver import frontend_check, project_headers


def test_active_abi_probe_uses_headers_and_configured_compiler_only(tmp_path,monkeypatch):
    import subprocess
    (tmp_path/'Makefile').write_text('test recipe')
    monkeypatch.setattr(frontend_check,'recipe',lambda *args:{'command':['clang','-DVERSION=1']})
    def run(command,**kwargs):
        assert '-DVERSION=1' in command
        from pathlib import Path
        probe=Path(command[-1]).read_text()
        assert probe=='#include "api.h"\n'
        assert 'SECRET' not in probe and 'wrong' not in probe
        ast={'inner':[{'kind':'FunctionDecl','name':'f','type':{'qualType':'int (void *, int)'},
            'inner':[{'kind':'ParmVarDecl','type':{'qualType':'void *'}},
                     {'kind':'ParmVarDecl','type':{'qualType':'int'}}]}]}
        return SimpleNamespace(returncode=0,stdout=json.dumps(ast),stderr='')
    monkeypatch.setattr(subprocess,'run',run)
    rows,report=project_headers.active_declarations(tmp_path,'f',
        '#include "api.h"\nint f(long wrong) { SECRET_BODY(); }','build/src/f.o',tmp_path)
    assert rows[0].prototype=='int f(void *, int)'
    assert report['candidate_body_supplied'] is False
    assert report['reference_body_supplied'] is False


def test_active_abi_probe_does_not_fall_back_to_textual_first_declaration(tmp_path,monkeypatch):
    import subprocess
    (tmp_path/'Makefile').write_text('test recipe')
    monkeypatch.setattr(frontend_check,'recipe',lambda *args:{'command':['clang']})
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=1,stdout='',stderr='missing dependency'))
    with pytest.raises(ValueError,match='probe rejected'):
        project_headers.active_declarations(tmp_path,'f','#include "api.h"','build/src/f.o',tmp_path)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout='{}',stderr=''))
    with pytest.raises(ValueError,match='unambiguous declaration'):
        project_headers.active_declarations(tmp_path,'f','#include "api.h"','build/src/f.o',tmp_path)


def test_conditional_prototypes_are_not_silently_selected(tmp_path,monkeypatch):
    from eval import dag_pipeline_pilot as dag
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text(
        '#if VERSION\nint f(int x);\n#else\nint f(void *x, int y);\n#endif\n')
    assert len(project_headers.declarations(tmp_path,'f',all_variants=True)) == 2
    unresolved=dag.prototype_info(tmp_path,'f')
    assert not unresolved['known'] and unresolved['arity'] is None
    arities,contracts=dag._call_contracts(tmp_path,['f'])
    assert not contracts['f']['arity_known']
    monkeypatch.setattr(project_headers,'active_declarations',lambda *a:(
        [project_headers.HeaderDeclaration('active','int f(void *, int)')],{'selected':True}))
    context=dict(source='#include "api.h"',target='build/src/f.o',ws=tmp_path)
    resolved=dag.prototype_info(tmp_path,'f',abi_context=context)
    assert resolved['arity']==2 and resolved['pointer_registers']==['a0']
    assert resolved['active_selection']=={'selected':True}
    assert dag._call_contracts(tmp_path,['f'],context)[0]['f']==2


def test_seven_argument_seeds_execute_stack_values_and_pointer(tmp_path):
    from eval import dag_pipeline_pilot as dag
    from solver import mips_differential as diff
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('int f(void *, void *, int, void *, int, void *, int);\n')
    abi=dag.prototype_info(tmp_path,'f')
    assert abi['argument_words']==7 and abi['pointer_registers']==['a0','a1','a3']
    cases=dag._seed_cases('f',abi)
    program=diff.Program.parse('f','''
        lw t0,20(sp)
        lw v0,24(sp)
        sw v0,0(t0)
        lbu v1,19(sp)
        jr ra
        nop
    ''')
    for case,value in zip(cases,(0,1,0xffffffff,0x80000000,0x7fffffff)):
        run=diff.execute_case(program,case,return_registers=('v0','v1'))
        assert run.status=='returned',run.error
        assert run.return_values=={'v0':value,'v1':value & 255}
        assert run.writes[-1].address=='arg5'
        assert run.writes[-1].value==value
    wrong=diff.Program.parse('wrong','jr ra\nli v0,9')
    assert diff.compare_programs(program,wrong,cases[1]).status=='failed'
    for location in ('@entrysp+0xc','@entrysp+0x11','@entrysp+0x40','@arg16'):
        with pytest.raises(ValueError):
            diff._scratch_address(location,4)
