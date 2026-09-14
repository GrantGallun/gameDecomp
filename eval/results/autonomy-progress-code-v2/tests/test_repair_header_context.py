from solver import project_headers


def test_repair_context_has_referenced_header_declarations_not_bodies(tmp_path):
    headers = tmp_path / "include"
    headers.mkdir()
    (headers / "types.h").write_text("""
extern short statusCodes[4];
extern int unrelated;
int callee(int x);
static int forbidden_body(void) { return 12345; }
""")
    source = '#include "types.h"\nint f(void) {statusCodes[0] = 1; return callee(2);}'
    context = project_headers.repair_context(tmp_path, source)
    assert "extern short statusCodes[4];" in context
    assert "int callee(int x);" in context
    assert "unrelated" not in context
    assert "12345" not in context and "forbidden_body" not in context
    assert "not editable" in context
