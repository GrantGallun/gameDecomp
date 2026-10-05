"""Line normalization for IDO stage keys (the compiler-backed parts run on the native pilot)."""
from solver import ido_stages


def test_every_line_reports_line_one_and_markers_are_dropped() -> None:
    pre = '# 1 "cand.c"\nint a;\n\n# 7 "x.h"\nint f(void) {\n    return a;\n}\n'
    out = ido_stages.one_line(pre)
    lines = out.splitlines()
    assert lines[0::2] == ['#line 1 "u.c"'] * 4
    assert lines[1::2] == ["int a;", "int f(void) {", "return a;", "}"]
    assert "# 7" not in out


def test_layout_differences_normalize_to_the_same_text() -> None:
    a = ido_stages.one_line('# 1 "c.c"\nint f(void) {\n    return 1;\n}\n')
    b = ido_stages.one_line('# 1 "c.c"\n\n\nint f(void) {\n\n        return 1;\n}\n')
    assert a == b


def test_no_single_giant_line() -> None:
    """The first version joined everything onto one ~28,000-char line, which IDO silently dropped."""
    pre = "\n".join(f"int v{i};" for i in range(3000))
    assert max(len(l) for l in ido_stages.one_line(pre).splitlines()) < 100


def test_a_pragma_declines_rather_than_moving() -> None:
    assert ido_stages.one_line("#pragma intrinsic(abs)\nint f(void) { return 0; }\n") is None
