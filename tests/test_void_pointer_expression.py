import pytest
from solver import repair_context as r

SOURCE='void f(void *p) {\n use((void *) (p - 0x10));\n}'

def test_explicit_void_pointer_expression_preserves_byte_units():
    rows=r.normalize(SOURCE,'Unacceptable operand of X.','f')
    assert rows==[('void-pointer-byte-expression',SOURCE.replace('(void *) (p - 0x10)',
        '(void *)((unsigned char *)p - 0x10)'))]
    assert not r.normalize(rows[0][1],'Unacceptable operand of X.','f')

@pytest.mark.parametrize('source', [SOURCE.replace('void *p','int *p'),
    SOURCE.replace('0x10','amount()'),SOURCE.replace('(p -','(p++ -'),
    SOURCE.replace('void *p','void *q'),SOURCE.replace('use(', 'int p;\n use(')])
def test_unsupported_expression_or_shadow_declines(source):
    assert not r.normalize(source,'Unacceptable operand of X.','f')

def test_no_rewrite_without_target_compiler_diagnostic():
    assert not r.normalize(SOURCE,'','f')
