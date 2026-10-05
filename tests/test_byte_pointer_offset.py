import pytest
from solver import rewrites


SOURCE = '''struct Record { char pad[60]; };
void f(struct Record *arg0) {
    consume((Transform *) (arg0 + 0x18));
}
'''
DIFF = '''@@ -1,2 +1,2 @@
-addiu a0,a0,0x18
+addiu a0,a0,0x5a0
'''


def test_scaled_parameter_offset_emits_byte_view_in_catalog():
    proposals = [r for r in rewrites.propose(SOURCE, DIFF) if r.kind == 'pointer-byte-offset']
    assert len(proposals) == 1
    assert '((unsigned char *)arg0 + 0x18)' in proposals[0](SOURCE)
    assert proposals[0](SOURCE + ' ') == SOURCE + ' '


def test_motivating_call_followed_by_guarded_draw():
    source = SOURCE.replace('    consume((Transform *) (arg0 + 0x18));',
        '    int matrix;\n    matrix = consume((Transform *) (arg0 + 0x18));\n'
        '    if (matrix != 0) {\n        draw(matrix, arg0);\n    }')
    proposals = [r for r in rewrites.propose(source, DIFF) if r.kind == 'pointer-byte-offset']
    assert len(proposals) == 1


def test_no_offset_correspondence_or_scale_mismatch_declines():
    for diff in [DIFF.replace('0x18', '0x20'), DIFF.replace('0x5a0', '0x18'),
                 DIFF.replace('0x5a0', '0x19'), DIFF.replace('a0,a0', 'sp,sp')]:
        assert not [r for r in rewrites.propose(SOURCE, diff) if r.kind == 'pointer-byte-offset']


def test_local_shadow_wide_abi_byte_type_or_noncode_declines():
    for source in [SOURCE.replace('struct Record *arg0)', 's64 first, struct Record *arg0)'),
                   SOURCE.replace('struct Record *arg0)', 'char *arg0)'),
                   SOURCE.replace('    consume', '    int arg0;\n    consume'),
                   SOURCE.replace('consume((Transform *) (arg0 + 0x18));', '/* (arg0 + 0x18) */'),
                   SOURCE.replace('arg0 + 0x18', 'arg0++ + 0x18')]:
        assert not [r for r in rewrites.propose(source, DIFF) if r.kind == 'pointer-byte-offset']


@pytest.mark.parametrize('statement', ['++arg0;', '--arg0;', '(arg0)++;',
    '(arg0) = other;', 'escape(&(arg0));', 'struct Record *other, *arg0;',
    'int other, arg0;', 'arg0 *= 2;', 'arg0 <<= 1;',
    '( (arg0) ) = other;', '++( (arg0));', 'escape(& ( (arg0)));'])
def test_parameter_identity_guard_handles_parentheses_and_comma_declarations(statement):
    source = SOURCE.replace('    consume', '    ' + statement + '\n    consume')
    assert not [r for r in rewrites.propose(source, DIFF) if r.kind == 'pointer-byte-offset']
