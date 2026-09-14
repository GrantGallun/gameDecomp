import hashlib
from solver.aggregate_scalar_repair import propose


def test_local_incomplete_tag_uses_measured_typedef():
    source='void f(void) {\n    struct Packet local;\n}\n'
    diag="candidate.c:2:19: error: variable has incomplete type 'struct Packet'\n    2 |     struct Packet local;\n"
    measured={'kind':'target-compiler-header-layouts','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
              'layouts':{'Packet':[{'member':'data','offset':0,'width':4,'owner_size':4}]}}
    assert '    Packet local;' in propose(source,'f',diag,measured)['source']
    assert not propose(source,'f',diag,{**measured,'layouts':{}})['changes']
    assert not propose(source,'f',diag.replace('2 |     struct','2 |    struct'),measured)['changes']
