import pytest
from solver import type_transaction as t


def test_callback_names_are_ignored_but_types_are_preserved():
    base='void f(Thread *t, void (*entry)(void *arg, int count), void *arg);'
    unnamed='void f(Thread *, void (*)(void *, int), void *);'
    assert t.signature(base,'f') == t.signature(unnamed,'f')
    assert t.signature(base,'f') is not None
    for changed in [base.replace('void (*entry)', 'int (*entry)'),
                    base.replace('int count','unsigned int count'),
                    base.replace('void *arg, int','int *arg, int')]:
        assert t.signature(changed,'f') != t.signature(base,'f')


@pytest.mark.parametrize('parameter',['void (*cb)()', 'void (*cb)(int, ...)',
    'void (**cb)(int)', 'void (*cb)(void (*inner)(int))', 'void (*cb[2])(int)'])
def test_unsupported_callback_forms_decline(parameter):
    assert t.signature('void f('+parameter+');','f') is None


def test_transaction_validates_callback_definition_not_inserted_prototype():
    source='void f(void (*cb)(int)) { cb(1); }'
    abi={'status':'locked','shape':t.signature(source,'f')}
    t.validate(source,source.replace('cb','renamed'),'f',abi)
    with pytest.raises(ValueError,match='public ABI lock'):
        t.validate(source,'void f(void (*cb)(int));\nvoid f(void (*cb)(unsigned int)) { cb(1); }','f',abi)
