from solver import project_headers


def test_reconciles_only_header_owned_top_level_declarations(tmp_path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "common.h").write_text('#include "sdk.h"\n')
    (inc / "sdk.h").write_text(
        "#ifndef SDK\n#define SDK\n"
        "extern int send(Queue *, int);\nextern Queue gQueue;\n"
        "extern unsigned char gPending;\n#endif\n")
    body = 'void f(void) { extern ? gQueue; const char *s = "};"; send(&gQueue, 1); }'
    draft = ('#include "common.h"\n? send(? *, ?); /* extern */\n'
             'extern ? gQueue;\nextern signed char gPending;\n'
             'extern ? gUnknown;\nint initialized = 1;\n' + body)
    result, removed = project_headers.reconcile_declarations(tmp_path, draft)
    assert removed == ["send", "gQueue", "gPending"]
    assert body in result
    assert '#include "common.h"' in result
    assert 'extern ? gUnknown;' in result
    assert 'int initialized = 1;' in result
    assert project_headers.reconcile_declarations(tmp_path, result)[1] == []


def test_unincluded_and_outside_headers_do_not_supply_declarations(tmp_path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "unused.h").write_text('extern int gUnused;\n')
    (tmp_path / "outside.h").write_text('extern int gOutside;\n')
    source = '#include "../outside.h"\nextern ? gOutside;\nextern ? gUnused;\n'
    assert project_headers.reconcile_declarations(tmp_path, source) == (source, [])


def test_preflight_adds_reconciled_variant_without_changing_body(tmp_path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "sdk.h").write_text('int send(Queue *, int);\nextern Queue gQueue;\n')
    source = '? send(? *, ?);\nextern ? gQueue;\nvoid f(void) { send(&gQueue, 1); }'
    rows = project_headers.preflight_variants(tmp_path, 'f', 'jal send', source)
    label, fixed = rows[-1]
    assert label == 'project-header-reconcile:send,gQueue'
    assert 'void f(void) { send(&gQueue, 1); }' in fixed
    assert '? send' not in fixed
    assert any('? send' in code for _, code in rows[:-1])


def test_multiline_declarations_and_comment_braces(tmp_path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "sdk.h").write_text('/* {; */\nint send(\n Queue *,\n int);\n')
    source = '#include "sdk.h"\n? send(\n? *, ?);\nvoid f(void) { /* }; */ }'
    result, removed = project_headers.reconcile_declarations(tmp_path, source)
    assert removed == ['send']
    assert 'void f(void) { /* }; */ }' in result


def test_sdk_c_linkage_wrapper_is_transparent(tmp_path):
    inc = tmp_path / 'include'
    inc.mkdir()
    (inc / 'sdk.h').write_text('#ifdef CPP\nextern "C" {\n#endif\n'
                              'int send(Queue *, int);\n#ifdef CPP\n}\n#endif\n')
    source = '#include "sdk.h"\n? send(? *, ?);\nvoid f(void) { send(0, 1); }'
    result, removed = project_headers.reconcile_declarations(tmp_path, source)
    assert removed == ['send']
    assert 'void f(void) { send(0, 1); }' in result


def test_address_inference_uses_known_call_not_guessed_identifier(tmp_path):
    inc = tmp_path / 'include'
    inc.mkdir()
    (inc / 'sdk.h').write_text('int send(Queue *queue, int);\n')
    body = 'void f(void) { send(&gMystery, 1); }'
    source = '#include "sdk.h"\nextern ? gMystery;\nextern ? gUnknown;\n' + body
    result, plans = project_headers.infer_address_externs(tmp_path, source)
    assert plans == ['gMystery=Queue']
    assert 'extern Queue gMystery;' in result
    assert 'extern ? gUnknown;' in result
    assert body in result


def test_conflicting_and_void_pointer_constraints_are_not_guessed(tmp_path):
    inc = tmp_path / 'include'
    inc.mkdir()
    (inc / 'sdk.h').write_text('int a(Queue *);\nint b(Thread *);\nint c(void *);\n')
    source = ('#include "sdk.h"\nextern ? gMystery;\nextern ? gUnknown;\n'
              'void f(void) { a(&gMystery); b(&gMystery); c(&gUnknown); }')
    assert project_headers.infer_address_externs(tmp_path, source) == (source, [])


def test_call_context_does_not_union_incompatible_api_headers(tmp_path):
    inc = tmp_path / 'include'
    inc.mkdir()
    (inc / 'api.h').write_text('int send(Queue *, int);\n')
    (inc / 'private.h').write_text('int send(void *, int);\n')
    assert project_headers.context_headers(tmp_path, 'f', 'jal send') == ['api.h']


def test_strings_cannot_supply_type_constraints(tmp_path):
    inc = tmp_path / 'include'
    inc.mkdir()
    (inc / 'api.h').write_text('int send(Queue *);\n')
    source = '#include "api.h"\nextern ? gQueue;\nconst char *s = "send(&gQueue)";'
    assert project_headers.infer_address_externs(tmp_path, source) == (source, [])
