from solver.compile_recovery import header_variant


def put(repo, name, text):
    path = repo / "include" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_does_not_add_conflicting_target_prototype_to_assembly_draft(tmp_path):
    # Clean-frame motivating shape: drawShopMenuSelectedModePanel.
    put(tmp_path, "game/panel.h", "void drawPanel(Panel *panel);\n")
    source = "void drawPanel(void *arg0) { }\n"
    candidate, report = header_variant(
        tmp_path, "drawPanel", "glabel drawPanel", source, "build/src/panel.o")
    assert candidate == source
    assert report["added"] == []


def test_still_adds_callee_header_and_reconciles_generated_stub(tmp_path):
    put(tmp_path, "game/panel.h", "void drawPanel(Panel *panel);\n")
    put(tmp_path, "callee.h", "int helper(int value);\n")
    source = "short helper(int);\nvoid drawPanel(void *arg0) { helper(1); }\n"
    candidate, report = header_variant(
        tmp_path, "drawPanel", "glabel drawPanel\njal helper", source, "build/src/panel.o")
    assert '#include "callee.h"' in candidate
    assert "game/panel.h" not in candidate
    assert "short helper(int);" not in candidate
    assert report["reconciled"] == ["helper"]


def test_retains_explicit_header_even_when_target_signature_conflicts(tmp_path):
    put(tmp_path, "game/panel.h", "void drawPanel(Panel *panel);\n")
    source = '#include "game/panel.h"\nvoid drawPanel(void *arg0) { }\n'
    candidate, _ = header_variant(
        tmp_path, "drawPanel", "glabel drawPanel", source, "build/src/panel.o")
    assert '#include "game/panel.h"' in candidate


def test_target_header_can_still_supply_a_used_type(tmp_path):
    put(tmp_path, "game/panel.h", "typedef struct { int x; } Panel;\nvoid drawPanel(Panel *panel);\n")
    source = "void drawPanel(void *arg0) { Panel *p; }\n"
    candidate, report = header_variant(
        tmp_path, "drawPanel", "glabel drawPanel", source, "build/src/panel.o")
    assert '#include "game/panel.h"' in candidate
    assert {"identifier": "Panel", "header": "game/panel.h"} in report["added"]


def test_compatible_by_value_alias_still_recovers_its_header(tmp_path):
    put(tmp_path, "mode.h", "typedef int Mode;\nvoid f(int mode);\n")
    candidate, report = header_variant(
        tmp_path, "f", "glabel f", "void f(Mode mode) {}\n", "build/src/f.o")
    assert '#include "mode.h"' in candidate


def test_compatible_return_alias_still_recovers_its_header(tmp_path):
    put(tmp_path, "mode.h", "typedef int Mode;\nint f(void);\n")
    candidate, _ = header_variant(
        tmp_path, "f", "glabel f", "Mode f(void) { return 0; }\n", "build/src/f.o")
    assert '#include "mode.h"' in candidate


def test_conflicting_target_header_is_retained_for_a_callee(tmp_path):
    put(tmp_path, "panel.h", "void drawPanel(Panel *panel);\nint helper(int value);\n")
    source = "void drawPanel(void *arg0) { helper(1); }\n"
    candidate, report = header_variant(
        tmp_path, "drawPanel", "glabel drawPanel\njal helper", source, "build/src/panel.o")
    assert '#include "panel.h"' in candidate
    assert {"identifier": "helper", "header": "panel.h"} in report["added"]


def test_compatible_enum_alias_keeps_header_recovery(tmp_path):
    put(tmp_path, "mode.h", "typedef enum Mode { MODE_A } Mode;\nvoid f(enum Mode mode);\n")
    candidate, _ = header_variant(
        tmp_path, "f", "glabel f", "void f(Mode mode) {}\n", "build/src/f.o")
    assert '#include "mode.h"' in candidate


def test_compatible_callback_alias_keeps_header_recovery(tmp_path):
    put(tmp_path, "callback.h", "typedef void (*Callback)(int);\nvoid f(void (*cb)(int));\n")
    candidate, _ = header_variant(
        tmp_path, "f", "glabel f", "void f(Callback cb) {}\n", "build/src/f.o")
    assert '#include "callback.h"' in candidate
