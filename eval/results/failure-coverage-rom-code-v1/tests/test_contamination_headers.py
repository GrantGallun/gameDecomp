import pytest

from solver import workspace


def test_independently_included_prototype_is_not_reference_body(tmp_path, monkeypatch):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('extern void startThread(Thread *);\n')
    line = 'extern void startThread(Thread *);'
    monkeypatch.setattr(workspace,'sh',lambda *a,**k:(0,'src/a.c:7:'+line))
    workspace.assert_uncontaminated('#include "api.h"\n'+line,tmp_path,'startThread')
    with pytest.raises(RuntimeError,match='CONTAMINATION'):
        workspace.assert_uncontaminated(line,tmp_path,'startThread')


@pytest.mark.parametrize('line', [
    'someLongFunctionCall(argument);',
    'extern int secretValue = 123456;',
    'extern void privateFunction(Thread *);',
    'thread->status = SOME_LONG_CONSTANT;',
])
def test_header_exception_does_not_whitelist_reference_statements(tmp_path,monkeypatch,line):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('extern void startThread(Thread *);\n')
    monkeypatch.setattr(workspace,'sh',lambda *a,**k:(0,'src/a.c:7:'+line))
    with pytest.raises(RuntimeError,match='CONTAMINATION'):
        workspace.assert_uncontaminated('#include "api.h"\n'+line,tmp_path,'startThread')
