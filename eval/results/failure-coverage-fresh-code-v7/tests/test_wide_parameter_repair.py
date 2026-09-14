from solver.wide_parameter_repair import propose


def test_header_wide_argument_keeps_working_words(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/api.h').write_text('typedef unsigned long long u64;\ntypedef u64 Time;\nvoid f(Time);\n')
    source='#include "api.h"\nvoid f(s32 time_unk0, u32 time_unk4) {\n    use(time_unk0,time_unk4);\n}'
    r=propose(tmp_path,source,'f',big_endian_o32=True)
    assert 'void f(Time time)' in r['source']
    assert 's32 time_unk0 = (s32) ((unsigned long long)time >> 32);' in r['source']
    assert 'use(time_unk0,time_unk4);' in r['source']
    assert propose(tmp_path,source,'f')['source']==source
    for bad in (source.replace('time_unk4','other_unk4'),source.replace('    use','    int time;\n    use')):
        assert propose(tmp_path,bad,'f',big_endian_o32=True)['source']==bad
    (tmp_path/'include/api.h').write_text('typedef unsigned int Time;\nvoid f(Time);\n')
    assert propose(tmp_path,source,'f',big_endian_o32=True)['source']==source
