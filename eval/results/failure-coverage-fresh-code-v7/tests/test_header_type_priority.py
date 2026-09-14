from solver.compile_obligations import header_types


def test_referenced_global_type_precedes_family_budget(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/types.h').write_text('''typedef struct { int a; int b; int c; } FamilyRecord;
typedef union { int value; unsigned int bits; } HiddenType;
extern HiddenType gValue;
''')
    source='#include "types.h"\nvoid updateFamily(void) { gValue = 0; }'
    result=header_types(tmp_path,source,'updateFamily',max_chars=65)
    assert [r['type'] for r in result]==['HiddenType']
    assert header_types(tmp_path,source.replace('gValue = 0;', ''),'updateFamily',max_chars=65)[0]['type']=='FamilyRecord'
