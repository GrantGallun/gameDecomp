import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "eval/results/edit-capability-20261002/recover_context.py"


def recovery():
    spec = importlib.util.spec_from_file_location("recover_context", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(rid, function):
    return {"id": rid, "repository": "dkr", "variant": "us", "file": "f.c", "function": function}


def test_resume_proves_completed_zero_and_nonzero_groups_from_log():
    a, b = row("a", "a"), row("b", "b")
    groups = [(("dkr", "us", "f.c", "a"), [a]), (("dkr", "us", "f.c", "b"), [b])]
    assert recovery().completed_prefix("0 0 2s\n1 1 3s\nTraceback\n", groups, [b]) == 2


def test_resume_refuses_a_log_claiming_unwritten_rows():
    a = row("a", "a")
    with pytest.raises(ValueError, match="count"):
        recovery().completed_prefix("1 1 3s\n", [(("dkr", "us", "f.c", "a"), [a])], [])


def test_resume_refuses_rows_from_an_uncompleted_group():
    a, b = row("a", "a"), row("b", "b")
    groups = [(("dkr", "us", "f.c", "a"), [a]), (("dkr", "us", "f.c", "b"), [b])]
    with pytest.raises(ValueError, match="group"):
        recovery().completed_prefix("1 1 3s\n", groups, [b])


def test_resume_rejects_changed_original_split_or_label():
    source = row("a", "a") | {"split": "dev", "label": "same"}
    changed = source | {"split": "train", "label": "differ"}
    with pytest.raises(ValueError, match="source"):
        recovery().completed_prefix("1 1 3s\n", [(("dkr", "us", "f.c", "a"), [source])], [changed])
