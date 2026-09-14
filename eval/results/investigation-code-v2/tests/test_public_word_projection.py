from solver.compile_recovery import project_signed_word_parameters as project
from solver.type_transaction import signature


def test_public_int_with_unchanged_s32_body_local():
    source='s32 f(void *p, s32 count) {\n    count += 1;\n    return count;\n}'
    expected=signature('s32 f(void *p, int count);','f')
    result=project(source,'f',expected,o32=True)
    assert 'int count_public_word' in result
    assert 's32 count = count_public_word;' in result
    assert 'count += 1;' in result
    assert signature(result,'f')==expected
    assert project(source,'f',expected)==source


def test_no_pointer_width_unsigned_or_name_collision_projection():
    source='s32 f(s32 count) { return count; }'
    for proto in ['s32 f(u32 count);','s32 f(s16 count);','s32 f(int *count);','int f(int count);']:
        assert project(source,'f',signature(proto,'f'),o32=True)==source
    source='s32 f(s32 count) { int count_public_word; return count; }'
    assert project(source,'f',signature('s32 f(int count);','f'),o32=True)==source
