from solver import modelrepair, workspace


def empty_attempt():
    return workspace.Attempt(False,0,False,'',
        'ERROR: Compiled object has no text symbols. Check for type conflicts or include issues.',
        '',7,frontend={'passed':True})


def test_empty_translation_unit_does_not_outrank_a_real_failing_draft():
    empty = empty_attempt()
    real = workspace.Attempt(False,0,False,'','Syntax Error','',8,
        frontend={'passed':False,'diagnostics':"candidate.c:1:1: error: unknown type\n"*8})
    assert modelrepair._quality(real) > modelrepair._quality(empty)
    assert modelrepair.compile_error_rank(real) < modelrepair.compile_error_rank(empty)


def test_no_text_candidate_does_not_crash_body_dependent_normalization(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace,'target_asm',lambda *a:'jr ra\nnop')
    result = modelrepair.search(tmp_path,'f','#include "common.h"\n',tmp_path,
        model='test',endpoint='none',base_attempt=empty_attempt(),resilient=True,max_calls=0)
    assert not result.exact and result.normalization_candidates == 0
    assert any('needs a function draft' in row for row in result.log)
