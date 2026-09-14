import pytest
from solver import repair_context


def test_definition_preserves_callback_parameter_and_return_spans():
    source = 'void f(Thread *t, void (*entry)(void *), void *arg) {\n entry(arg);\n}\n'
    match,end = repair_context.definition(source,'f')
    assert match[1]=='void '
    assert match[2]=='Thread *t, void (*entry)(void *), void *arg'
    assert source[match.end():end]=='\n entry(arg);\n}'


def test_definition_supports_nested_callback_and_ignores_prototype():
    source='void f(void (*cb)(int (*nested)(void)));\nvoid f(void (*cb)(int (*nested)(void))) {}'
    match,end=repair_context.definition(source,'f')
    assert match[2]=='void (*cb)(int (*nested)(void))'
    assert end==len(source)


@pytest.mark.parametrize('source', ['// m2c failed\n', 'void f(void (*cb)(void) {}',
    'void f(int x);', 'void f(void) {}\nvoid f(void) {}', 'void f(x) int x; {}'])
def test_definition_declines_missing_unbalanced_duplicate_or_old_style(source):
    with pytest.raises(ValueError):
        repair_context.definition(source,'f')
