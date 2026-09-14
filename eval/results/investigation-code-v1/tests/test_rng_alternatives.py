from pathlib import Path
from solver import rng_alternatives


def seed():
    return (Path(__file__).resolve().parents[1] / 'eval/results/last-push-final/__MusIntRandom.c').read_text()


def test_reuses_quotient_without_reassociating_divisions():
    variants = rng_alternatives.candidates(seed(), '__MusIntRandom', 2)
    assert len(variants) == 2
    assert 'code_shape_stage = (f32) mus_random_seed / 65536.0f;' in variants[0].source
    assert 'code_shape_stage /= 65536.0f;' in variants[0].source
    assert 'code_shape_value * code_shape_stage' in variants[0].source
    assert variants[1].label.endswith('+counted-loop')
    assert 'for (var_v0 = 0; var_v0 != 8;)' in variants[1].source


def test_counted_loop_rejects_unproved_termination_or_control_flow():
    src = seed()
    assert rng_alternatives.counted_loop(src.replace('var_v0 != 8', 'var_v0 != 7'), '__MusIntRandom') is None
    assert rng_alternatives.counted_loop(src.replace('var_v0 += 4', 'var_v0 += 0'), '__MusIntRandom') is None
    assert rng_alternatives.counted_loop(src.replace('temp_v1 =', 'continue; temp_v1 =', 1), '__MusIntRandom') is None
    assert rng_alternatives.counted_loop(src.replace('temp_v1 =', 'var_v0 = 8; temp_v1 =', 1), '__MusIntRandom') is None


def test_bounds_and_unrecognized_sources():
    assert not rng_alternatives.candidates(seed(), '__MusIntRandom', 0)
    assert not rng_alternatives.candidates(seed(), 'other_function')
    assert not rng_alternatives.candidates(seed().replace('f32 code_shape_stage', 'volatile f32 code_shape_stage'), '__MusIntRandom')
    assert not rng_alternatives.candidates('int f(int x) { return x; }', 'f')


def test_does_not_edit_comments_or_another_function():
    src = 'int other_function(void) { return 0; }\n' + seed()
    assert not rng_alternatives.candidates(src, 'other_function')
    assert rng_alternatives.counted_loop(src, 'other_function') is None
    commented = 'int f(void) { /* ' + seed()[seed().index('s32 __MusIntRandom'): ] + ' */ return 0; }'
    assert not rng_alternatives.candidates(commented, 'f')


def test_counted_loop_preserves_literals_and_comments():
    src = seed().replace('temp_v1 = mus_random_seed', 'sink("hello"); sink_char(\'x\'); /* keep */ temp_v1 = mus_random_seed', 1)
    result = rng_alternatives.counted_loop(src, '__MusIntRandom')
    assert 'sink("hello"); sink_char(\'x\'); /* keep */' in result
