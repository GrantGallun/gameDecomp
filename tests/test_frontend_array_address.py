from solver.frontend_repair import propose


def diagnostic(line, to='s16 *', from_='s16 (*)[4]'):
    return f"candidate.c:2:7: error: incompatible pointer types assigning to '{to}' (aka 'short *') from '{from_}'\n    2 | {line}\n"


def test_plain_array_address_decay(tmp_path):
    line='    p = &array;'
    source='void f(void) {\n'+line+'\n}\n'
    result=propose(tmp_path,source,'f',diagnostic(line))
    assert result['source']==source.replace('&array','array')
    assert result['changes'][0]['kind']=='one-dimensional-array-address-decay'


def test_array_arithmetic_mismatches_and_stale_evidence_decline(tmp_path):
    for line,to,from_ in [('    p = &array + 1;','s16 *','s16 (*)[4]'),
                          ('    p = &array;','s32 *','s16 (*)[4]'),
                          ('    p = &array;','s16 *','s16 (*)[3][4]')]:
        source='void f(void) {\n'+line+'\n}\n'
        assert not propose(tmp_path,source,'f',diagnostic(line,to,from_))['changes']
    source='void f(void) {\n    p = &other;\n}\n'
    assert not propose(tmp_path,source,'f',diagnostic('    p = &array;'))['changes']
