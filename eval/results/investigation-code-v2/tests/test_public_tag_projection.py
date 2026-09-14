from solver.compile_recovery import project_header_tag_parameters,header_tag_definitions
from solver.type_transaction import signature


def test_same_header_record_alias_restores_exact_public_tag():
    source='void f(Player *p) { p->x = 1; }'
    expected=signature('void f(struct Player *p);','f')
    headers=[{'type':'Player','definition':'typedef struct Player { int x; } Player;'}]
    result=project_header_tag_parameters(source,'f',expected,headers)
    assert result=='void f(struct Player *p) { p->x = 1; }'
    assert signature(result,'f')==expected


def test_wrong_or_missing_alias_never_relaxes_public_type():
    source='void f(Player *p) { }'
    expected=signature('void f(struct Player *p);','f')
    for headers in [[],[{'type':'Player','definition':'typedef struct Other { int x; } Player;'}]]:
        assert project_header_tag_parameters(source,'f',expected,headers)==source


def test_exact_alias_lookup_does_not_depend_on_prompt_packet_size(tmp_path):
    (tmp_path/'include').mkdir()
    body='typedef struct Player {\n'+''.join(f'int field{i};\n' for i in range(2000))+'} Player;'
    (tmp_path/'include'/'player.h').write_text(body)
    headers=header_tag_definitions(tmp_path,'#include "player.h"\n',{'Player'})
    assert len(headers)==1 and headers[0]['definition']==body
