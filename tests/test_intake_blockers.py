import pytest

from eval.intake_probe import classify_residual


@pytest.mark.parametrize('message, expected', [
    ('subscripted value is not an array, pointer, or vector', 'non-pointer-subscript'),
    ("member reference type 'Foo' is not a pointer; did you mean to use '.'?", 'non-pointer-arrow'),
    ("array type 'short[64]' is not assignable", 'array-assignment'),
    ('too many arguments to function call, expected 1, have 2', 'call-arity'),
    ("invalid operands to binary expression ('void *' and 'int')", 'invalid-binary-operands'),
    ("member reference base type 'void' is not a structure or union", 'member-on-void'),
    ("member reference base type 's32' (aka 'long') is not a structure or union", 'member-on-scalar-or-array'),
])
def test_names_operation_failures_instead_of_generic_syntax(message, expected):
    assert classify_residual(message) == expected


def test_inventory_counts_functions_and_keeps_object_mismatches_visible():
    from eval.intake_blockers import inventory
    diagnostic = {'line': 2, 'column': 3, 'what': 'subscripted value is not an array, pointer, or vector'}
    rows = [dict(function='blocked', source='void f() {\n x[0];\n}',
                 verdict={'compiled': False, 'exact': False},
                 frontend={'status': 'rejected', 'error_count': 2, 'errors': [diagnostic, diagnostic]}, trace=[]),
            dict(function='mismatch', source='', verdict={'compiled': True, 'exact': False, 'score': 99,
                 'diff': '--- target\n+++ candidate\n@@ -1 +1 @@\n-addiu v0,v0,1\n+addiu v0,v0,2\n'},
                 frontend={'status': 'passed', 'error_count': 0, 'errors': []}, trace=[]),
            dict(function='unknown', source='', verdict={'compiled': False, 'exact': False},
                 frontend={'status': 'unavailable', 'errors': []}, trace=[])]
    result = inventory(rows)
    assert result['stages'] == {'compilation-blocked': 1, 'object-mismatch': 1, 'frontend-unavailable': 1}
    assert result['frontend_classes']['non-pointer-subscript'] == {'functions': 1, 'diagnostics': 2}
    assert result['object_classes']['immediate'] == {'functions': 1, 'observations': 1}
    assert result['rows'][0]['sites'][0]['source_line'] == ' x[0];'
    assert result['rows'][1]['object_profile']['immediate'] == 1
