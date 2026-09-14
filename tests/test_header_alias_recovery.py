from solver.compile_recovery import header_variant


def test_simple_typedef_alias_header_recovered(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/actor.h').write_text('typedef struct { int x; } BaseActor;\ntypedef BaseActor Actor;\n')
    source='void f(Actor *p) { p->x = 0; }'
    result,report=header_variant(tmp_path,'f','',source,'build/src/f.o')
    assert '#include "actor.h"' in result
    assert {'identifier':'Actor','header':'actor.h'} in report['added']


def test_commented_alias_not_header_evidence(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/actor.h').write_text('/* typedef BaseActor Actor; */\n')
    _,report=header_variant(tmp_path,'f','','void f(Actor *p) {}','build/src/f.o')
    assert not report['added']
