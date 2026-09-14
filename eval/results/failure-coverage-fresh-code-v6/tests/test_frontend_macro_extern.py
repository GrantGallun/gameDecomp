from solver import frontend_repair


SOURCE = 'extern M2C_UNK gAlias;\nvoid f(void) { use(gAlias); }\n'
DIAG = "candidate.c:1:16: error: expected ')'\n    1 | extern M2C_UNK gAlias;\ninclude/object.h:3:17: note: expanded from macro 'gAlias'\n"


def test_header_field_macro_removes_only_redundant_extern(tmp_path):
    (tmp_path/'include').mkdir()
    (tmp_path/'include/object.h').write_text('extern Object gObject;\n#define gAlias (gObject.member)\n')
    result = frontend_repair.propose(tmp_path, SOURCE, 'f', DIAG)
    assert result['source'] == '\nvoid f(void) { use(gAlias); }\n'
    assert result['changes'][0]['kind'] == 'header-field-macro-extern'
    assert result['macro_headers']['gAlias']['backing_object'] == 'gObject'


def test_macro_removal_declines_missing_evidence(tmp_path):
    (tmp_path/'include').mkdir()
    header = tmp_path/'include/object.h'
    for text in ['#define gAlias (gObject.member)\n',
                 'extern Object gObject;\n#define gAlias 42\n',
                 'extern Object gObject;\n#define gAlias(x) (gObject.member)\n']:
        header.write_text(text)
        assert not frontend_repair.propose(tmp_path, SOURCE, 'f', DIAG)['changes']
    header.write_text('extern Object gObject;\n#define gAlias (gObject.member)\n')
    assert not frontend_repair.propose(tmp_path, SOURCE, 'f', DIAG.replace('1 | extern', '1 | static'))['changes']
    assert not frontend_repair.propose(tmp_path, SOURCE, 'f', DIAG.replace("macro 'gAlias'", "macro 'other'"))['changes']
