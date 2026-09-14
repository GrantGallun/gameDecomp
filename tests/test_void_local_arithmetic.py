import pytest
from solver import repair_context


def proposal(source):
    return dict(repair_context.normalize(source,'Unacceptable operand of X.','f')).get('void-local-byte-arithmetic')


def test_indexed_void_local_arithmetic_preserves_signature_and_expression():
    source='void f(int i) {\n    void *base;\n    base = get();\n    use(base + (i * 3));\n}'
    result=proposal(source)
    assert result == source.replace('base +','((unsigned char *)base) +')


@pytest.mark.parametrize('declarations,expression',[
    ('int *base;', 'base + 4'),
    ('void *base;\n    int *base;', 'base + 4'),
    ('void *base;', 'object->base + 4'),
    ('void *base;', 'object-> base + 4'),
    ('void *base;', 'base++'),
    ('void *base;', 'base += 4'),
])
def test_nonlocal_shadowed_typed_and_increment_uses_are_not_rewritten(declarations,expression):
    assert proposal('void f(void) {\n    '+declarations+'\n    use('+expression+');\n}') is None


def test_comments_and_strings_are_not_rewritten():
    source='void f(void) {\n    void *base;\n    /* base + 4 */\n    use("base + 4");\n}'
    assert proposal(source) is None


def test_typed_pointer_assignment_retains_original_void_result_conversion():
    source='void f(int i) {\n    void *base;\n    u16 *out;\n    out = base + (i * 2);\n}'
    assert 'out = (void *)(((unsigned char *)base) + (i * 2));' in proposal(source)
