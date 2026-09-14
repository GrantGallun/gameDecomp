"""Validation boundaries for isolated patch/full-function experiments."""
import importlib
import json

import pytest

validate_output = importlib.import_module(
    "eval.experiments.campaign-gap-audit.repair_output_pilot").validate_output

SOURCE = '#include "types.h"\nint outside = 7;\nint f(int x) { return x + 1; }\n'


def response(arm, old="x + 1", new="x + 2", function=None):
    packet = {"kind": "expression", "hypothesis": "correct increment"}
    if arm == "full_function":
        packet["function"] = function or "int f(int x) { return x + 2; }"
    else:
        packet["edits"] = [{"old": old, "new": new}]
    return json.dumps(packet)


@pytest.mark.parametrize("arm", ["patch", "compact_patch", "full_function"])
def test_normal_output_preserves_shared_signature_and_outer_source(arm):
    assert validate_output(SOURCE, "f", response(arm), arm) == SOURCE.replace("x + 1", "x + 2")


def test_full_function_outer_whitespace_is_harmless():
    result = validate_output(SOURCE, "f", response("full_function",
        function="\nint f(int x) { return x + 2; }\n"), "full_function")
    assert result == SOURCE.replace("x + 1", "x + 2")


@pytest.mark.parametrize("arm", ["patch", "full_function"])
def test_reject_signature_change_even_with_unchanged_forward_declaration(arm):
    source = "int f(int x);\n" + SOURCE
    text = response(arm, old="int f(int x) {", new="long f(int x) {",
                    function="long f(int x) { return x + 2; }")
    with pytest.raises(ValueError, match="signature"):
        validate_output(source, "f", text, arm)


@pytest.mark.parametrize("arm", ["patch", "full_function"])
def test_reject_noop(arm):
    text = response(arm, new="x + 1", function="int f(int x) { return x + 1; }")
    with pytest.raises(ValueError):
        validate_output(SOURCE, "f", text, arm)


@pytest.mark.parametrize("old,new", [
    ("outside = 7", "outside = 8"),
    ('#include "types.h"', '#include "other.h"'),
])
def test_patch_rejects_outside_edits(old, new):
    with pytest.raises(ValueError):
        validate_output(SOURCE, "f", response("patch", old=old, new=new), "patch")


@pytest.mark.parametrize("prefix", ['#include "other.h"\n', 'int outside = 8;\n'])
def test_full_function_rejects_extra_source(prefix):
    with pytest.raises(ValueError):
        validate_output(SOURCE, "f", response("full_function",
            function=prefix + "int f(int x) { return x + 2; }"), "full_function")


@pytest.mark.parametrize("arm", ["patch", "full_function"])
def test_reject_include_inside_function(arm):
    text = response(arm, old="return x + 1;", new='#include "other.h"\nreturn x + 2;',
                    function='int f(int x) {\n#include "other.h"\nreturn x + 2; }')
    with pytest.raises(ValueError):
        validate_output(SOURCE, "f", text, arm)
