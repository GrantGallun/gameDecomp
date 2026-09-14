import pytest
from solver import modelrepair, workspace, compile_recovery


def test_ido_compilation_does_not_disable_missing_header_recovery(monkeypatch, tmp_path):
    include = tmp_path/'include'/'PRinternal'
    include.mkdir(parents=True)
    (include/'helper.h').write_text('int __helper(void);\n')
    source = 'int f(void) { return __helper(); }'
    root = workspace.Attempt(True, 90, False, '', '', '', 7,
        frontend={'passed':False,'diagnostics':"error: implicit declaration of function '__helper'"},
        compiler_recipe={'target':'build/src/ultra/f.o'})
    good = workspace.Attempt(True, 90, False, '', '', '', 8, frontend={'passed':True})
    monkeypatch.setattr(workspace, 'target_asm', lambda *a:'glabel f\njal __helper\nnop\njr ra\nnop')
    recorded = []
    def score(*args, **kwargs):
        recorded.append((args[3],kwargs))
        return good
    monkeypatch.setattr(workspace, 'score', score)
    result = modelrepair.search(tmp_path,'f',source,tmp_path,model='test',endpoint='none',
        base_attempt=root,resilient=True,max_calls=0,compile_only=True)
    assert len(recorded) == 2
    assert recorded[0][0].startswith('int __helper(void);')
    code, receipt = recorded[1]
    assert '#include "PRinternal/helper.h"' in code
    assert source in code
    assert receipt['parent_attempt_id'] == 7
    assert receipt['extra']['header_context_hypotheses']['added'] == [
        {'identifier':'__helper','header':'PRinternal/helper.h'}]
    assert result.calls_attempted == 0
    assert result.best_attempt.frontend['passed']


@pytest.mark.parametrize('declarations', [
    '', 'int __helper();', 'int __helper(void *p);',
    'Unknown __helper(void);', 'int __helper(int, ...);',
    'int __helper(void);\nlong __helper(void);'])
def test_projection_declines_missing_or_complex_or_conflicting_headers(tmp_path, declarations):
    (tmp_path/'include').mkdir()
    (tmp_path/'include'/'a.h').write_text(declarations)
    source = 'int f(void) { return __helper(); }'
    candidate, report = compile_recovery.scalar_header_prototypes(tmp_path, source,
        "implicit declaration of function '__helper'")
    assert candidate == source
    assert report['declines'] and not report['prototypes']
