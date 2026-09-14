from solver import aggregate_scalar_repair as r
import hashlib


def bind(source, measured):
    return {**measured,'kind':'target-compiler-header-layouts',
            'source_sha256':hashlib.sha256(source.encode()).hexdigest()}

SOURCE='void f(void) {\n    local.vector = value;\n}\n'
DIAG="candidate.c:2:18: error: assigning to 'Vector' (aka 'struct Vector') from incompatible type 's32' (aka 'long')\n    2 |     local.vector = value;\n"
MEASURED={'layouts':{'Vector':[{'member':'x','offset':0,'width':4,'spelling':'s32','pointer':False,'array':False}]}}
MEASURED=bind(SOURCE,MEASURED)


def test_first_member_assignment():
    assert 'local.vector.x = value' in r.propose(SOURCE,'f',DIAG,MEASURED)['source']


def test_stale_and_ambiguous_layout_decline():
    assert not r.propose(SOURCE.replace('value','other'),'f',DIAG,MEASURED)['changes']
    assert not r.propose(SOURCE,'f',DIAG,bind(SOURCE,{'layouts':{'Vector':MEASURED['layouts']['Vector']*2}}))['changes']
    assert not r.propose(SOURCE,'f',DIAG.replace("'s32'","'u32'"),MEASURED)['changes']


def test_first_array_element_coordinated_read_write():
    s='void f(void) {\n Matrix local;\n local = (s16) ((s16) local / 2);\n}\n'
    line=s.splitlines()[2]
    d=f"candidate.c:3:{line.rindex('local')+1}: error: operand of type 'Matrix' where arithmetic or pointer type is required\n 3 | {line}\n"
    m={'layouts':{'Matrix':[{'member':'rotation','offset':0,'canonical':'s16[9]','array':True,'pointer':False,'width':18}]}}
    result=r.propose(s,'f',d,bind(s,m))
    assert 'local.rotation[0] = (s16) ((s16) local.rotation[0] / 2)' in result['source']
    m['layouts']['Matrix'][0]['width']=16
    assert not r.propose(s,'f',d,bind(s,m))['changes']


def test_scalar_read_and_measurement_identity():
    s='void f(void) {\n    Vector v;\n    out = (s32) v;\n}\n'
    line=s.splitlines()[2]
    d=f"candidate.c:3:{line.index('v;')+1}: error: operand of type 'Vector' where arithmetic or pointer type is required\n 3 | {line}\n"
    m=bind(s,MEASURED)
    assert 'out = (s32) v.x;' in r.propose(s,'f',d,m)['source']
    assert not r.propose(s,'f',d,MEASURED)['changes']
    for modified in [s.replace('Vector v','Other v'),s.replace('void f(void)','void f(Vector v)')]:
        assert not r.propose(modified,'f',d,bind(modified,m))['changes']
    assert not r.propose(s,'f',d,bind(s,{'layouts':{'Vector':MEASURED['layouts']['Vector']*2}}))['changes']
