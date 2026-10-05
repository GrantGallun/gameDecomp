import pytest
from solver import call_arity_repair as repair


def setup_case(tmp_path, statement='return tick(value, spare);', prototype='int tick(int value);', count=1):
    (tmp_path / 'include').mkdir(exist_ok=True)
    (tmp_path / 'include/api.h').write_text(prototype + '\n')
    source = '#include "api.h"\nint f(int value, int spare) {\n    ' + statement + '\n}\n'
    line = source.splitlines()[2]
    word = 'single argument' if count == 1 else str(count)
    diag = f'candidate.c:3:{line.index("spare")+1}: error: too many arguments to function call, expected {word}, have 2\n 3 | {line}\n'
    return source, diag


def test_positive_return_call_retains_excess_expression_evaluation(tmp_path):
    source, diag = setup_case(tmp_path)
    result = repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)
    assert 'return ((void)(spare), tick(value));' in result['source']
    assert result['changes'][0]['callee'] == 'tick'
    assert result['changes'][0]['target_calls'] == [0]
    assert result['changes'][0]['header_prototypes'] == ['int tick(int value);']


def test_nested_call_and_zero_arity_keep_expression_shape(tmp_path):
    source, diag = setup_case(tmp_path, 'return 3 + tick(value, spare);', 'int tick(void);', 0)
    result = repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)
    assert 'return 3 + ((void)(value), (void)(spare), tick());' in result['source']


@pytest.mark.parametrize('prototype', ['int tick(u64 value);', 'int tick(double value);',
    'int tick(Custom value);', 'int tick(int value, ...);', 'int tick();',
    'int tick(int value);\nint tick(void *value);'])
def test_wide_unknown_variadic_or_conflicting_contract_declines(tmp_path, prototype):
    source, diag = setup_case(tmp_path, prototype=prototype)
    assert not repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']


@pytest.mark.parametrize('extra', ['spare++', 'consume(spare)', '*spare', '(spare = 2)', 'spare + 4'])
def test_excess_expression_outside_closed_atoms_declines(tmp_path, extra):
    source, diag = setup_case(tmp_path, f'return tick(value, {extra});')
    assert not repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']


def test_missing_target_stale_location_wrong_abi_or_count_declines(tmp_path):
    source, diag = setup_case(tmp_path)
    for assembly, diagnostic, abi in [('jal other\nnop\n', diag, True),
            ('jal tick\nnop\n', diag.replace(' |     return', ' |    return'), True),
            ('jal tick\nnop\n', diag, False),
            ('jal tick\nnop\n', diag.replace('have 2', 'have 3'), True)]:
        assert not repair.propose(tmp_path, source, 'f', diagnostic, assembly, big_endian_o32=abi)['changes']


def test_indirect_or_shadowed_name_declines(tmp_path):
    source, diag = setup_case(tmp_path)
    source = source.replace('int value, int spare', 'int tick, int spare')
    assert not repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']


@pytest.mark.parametrize('typedef', ['typedef unsigned int Word;', 'typedef void (*Word)(void *);'])
def test_header_resolved_word_typedef(tmp_path, typedef):
    source, diag = setup_case(tmp_path, prototype=typedef + '\nint tick(Word value);')
    assert repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']


@pytest.mark.parametrize('typedef', ['typedef u64 Word;', 'typedef double Word;',
    'typedef unsigned int Word;\ntypedef u64 Word;'])
def test_wide_or_conflicting_typedef_declines(tmp_path, typedef):
    source, diag = setup_case(tmp_path, prototype=typedef + '\nint tick(Word value);')
    assert not repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']


@pytest.mark.parametrize('declaration', ['int (*tick)(int value);', 'Callback tick;',
    'int value2, (*tick)(int);'])
def test_local_callback_declarator_cannot_bind_to_global_target(tmp_path, declaration):
    source, diag = setup_case(tmp_path, declaration + ' return tick(value, spare);')
    assert not repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']


@pytest.mark.parametrize('wide', ['typedef struct { int hi; int lo; } Word;',
    'typedef int Word[2];', 'typedef int Other, Word[2];'])
def test_unsupported_conditional_typedef_is_not_ignored(tmp_path, wide):
    header = '#if WIDE\n' + wide + '\n#else\ntypedef unsigned int Word;\n#endif\nint tick(Word value);'
    source, diag = setup_case(tmp_path, prototype=header)
    assert not repair.propose(tmp_path, source, 'f', diag, 'jal tick\nnop\n', big_endian_o32=True)['changes']
