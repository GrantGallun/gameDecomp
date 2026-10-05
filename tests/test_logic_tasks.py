import pytest

from eval import logic_tasks as lt
from eval import repair_prompts

ORIG = "s32 f(s32 *p) {\n    s32 x;\n    x = p[1] + 60;\n    p[2] = x;\n    return x;\n}"
DIFF = "--- t.s\n+++ c.s\n@@ -1,3 +1,3 @@\n-addiu v0,v0,0x3c\n+addiu v0,v0,0x3d\n lw t6,0x4(a0)"


def test_diff_rows_ignores_blank_lines_in_model_answers():
    answer = "DIFFER\n\n-lw v0,0(a0)\n\n+lh v0,0(a0)\n\nExplanation."
    assert lt.diff_rows(answer) == ["-lw v0,0(a0)", "+lh v0,0(a0)"]


CONTEXT = "typedef int s32;\ntypedef short s16;\nstruct Obj {\n  s16 a;\n  s32 b;\n};\n"


def _row(pert, label="differ", diff=DIFF, rid="const::r:f.c:f", repo="dkr", split="train", facts=()):
    return {"id": rid, "class": "const", "label": label, "original_def": ORIG, "perturbed_def": pert,
            "original_fn": ORIG, "perturbed_fn": pert, "context": CONTEXT, "facts": list(facts),
            "diff": diff if label == "differ" else "", "opt": "-O2 -mips2", "split": split,
            "split_group": "r:f.c", "repository": repo, "function": "f", "source_kind": "public-planted"}


def _fact(name, old, new, relevant, label="differ", diff=DIFF):
    return {"name": name, "old": old, "new": new, "kind": "signedness",
            "context": CONTEXT.replace(f"  {old} {name};", f"  {new} {name};"),
            "label": label, "diff": diff, "label_flips": False, "rows_change": relevant}


def test_a_row_without_checked_context_is_refused():
    row = _row(ORIG.replace("60", "61"))
    del row["context"]
    tasks, tally = lt.build([row])
    assert tasks == [] and tally["refused-no-context"] == 2


def test_prompts_carry_the_context():
    t = lt.predict_task(_row(ORIG.replace("60", "61")))
    assert "struct Obj {" in t["prompt"] and "DECLARATIONS IN SCOPE" in t["prompt"]


def test_withhold_hides_exactly_the_retyped_declaration():
    f = _fact("a", "s16", "u16", True)
    shown = lt.withhold(CONTEXT, f["context"], "s16")
    assert "  ?? a;" in shown and "typedef short s16;" in shown and shown.count("??") == 1


def test_a_relevant_fact_yields_a_twin_and_one_need_and_an_irrelevant_one_a_control():
    other = "+addiu v0,v0,0x40"
    facts = [_fact("a", "s16", "u16", True, diff=DIFF.replace("+addiu v0,v0,0x3d", other)),
             _fact("b", "s32", "u32", False)]
    tasks, _ = lt.build([_row(ORIG.replace("60", "61"), facts=facts)])
    need = [t for t in tasks if t["kind"] == repair_prompts.LOGIC_NEED_KIND]
    assert sorted(t["completion"].split("\n")[0] for t in need) == ["DIFFER", "NEED: a"]
    twin = [t for t in tasks if ":twin:" in t["id"]]
    assert len(twin) == 1 and "0x40" in twin[0]["completion"] and "u16 a;" in twin[0]["prompt"]
    assert all("??" in t["prompt"] for t in need)


def test_withheld_type_is_explicitly_limited_to_the_two_compiled_alternatives():
    fact = _fact("b", "s32", "u32", False)
    tasks, _ = lt.build([_row(ORIG.replace("60", "61"), facts=[fact])])
    need = next(t for t in tasks if t["kind"] == repair_prompts.LOGIC_NEED_KIND)
    assert "`s32` or `u32`" in need["prompt"]
    assert "only these two possibilities" in need["prompt"]


@pytest.mark.parametrize("pert", [
    ORIG.replace("60", "61"),                                       # replace
    ORIG.replace("    p[2] = x;\n", ""),                            # dropped statement -> insert
    ORIG.replace("    return x;", "    s32 t;\n    t = x;\n    return t;"),   # replace 1 line by 3
    ORIG.replace("    x = p[1] + 60;\n    p[2] = x;", "    p[2] = x;\n    x = p[1] + 60;"),  # swap
])
def test_edit_script_reproduces_the_original(pert):
    script = lt.edit_script(pert, ORIG)
    assert script
    assert lt.apply_script(pert, "\n".join(script)) == ORIG


def test_apply_script_refuses_what_it_cannot_parse():
    assert lt.apply_script(ORIG, "REPLACE 99: x") is None
    assert lt.apply_script(ORIG, "change line 3") is None
    assert lt.apply_script(ORIG, "DELETE 0") is None


def test_explain_task_states_the_edit_and_never_the_answer_line():
    row = _row(ORIG.replace("60", "61"))
    t = lt.explain_task(row)
    assert t["kind"] == repair_prompts.LOGIC_EXPLAIN_KIND
    assert t["completion"] == "REPLACE 3:     x = p[1] + 60;"
    assert "p[1] + 60" not in t["prompt"] and "+addiu v0,v0,0x3d" in t["prompt"]
    assert "---" not in t["prompt"].split("INSTRUCTION DIFF")[1]          # file headers stripped


def test_explain_refuses_a_prompt_that_leaks_the_answer():
    row = _row(ORIG.replace("60", "61"), diff=DIFF + "\n+    x = p[1] + 60;")
    with pytest.raises(lt.Leak):
        lt.explain_task(row)


def test_predict_orients_the_rows_to_a_and_b():
    for rid in (f"const::r:f.c:f{i}" for i in range(8)):
        t = lt.predict_task(_row(ORIG.replace("60", "61"), rid=rid))
        a = t["prompt"].split("FUNCTION A:")[1].split("FUNCTION B:")[0]
        rows = t["completion"].split("\n")[1:]
        minus = next(r for r in rows if r.startswith("-"))
        # the `-` row belongs to A: 0x3c is the original's 60, 0x3d the planted 61
        assert ("0x3c" in minus) == ("60;" in a)
        assert ">> " in a


def test_erased_edit_is_labelled_same_and_gets_no_explain_task():
    tasks, _ = lt.build([_row(ORIG.replace("p[1] + 60", "60 + p[1]"), label="same")])
    assert [t["completion"] for t in tasks] == ["SAME"]


def test_splits_follow_the_corpus_and_the_holdout_repository():
    rows = [_row(ORIG.replace("60", "61"), rid="a", split="dev"),
            _row(ORIG.replace("60", "61"), rid="b", repo="sm64"),
            _row(ORIG.replace("60", "61"), rid="c")]
    tasks, _ = lt.build(rows, holdout_repos=["sm64"])
    assert {t["id"].split(":", 1)[1]: t["split"] for t in tasks} == {"a": "exam", "b": "check", "c": "train"}


@pytest.mark.parametrize("split", ["test", "heldout", "check", None])
def test_unknown_splits_are_refused_not_sent_to_train(split):
    # audit 2026-10-03: every non-`dev` label used to become train
    with pytest.raises(lt.UnknownSplit):
        lt.build([_row(ORIG.replace("60", "61"), split=split)])


def test_duplicate_input_ids_are_refused():
    row = _row(ORIG.replace("60", "61"))
    with pytest.raises(ValueError, match="more than once"):
        lt.build([row, dict(row)])


def test_an_over_long_diff_is_refused_not_truncated():
    long = "\n".join(f"-addiu v0,v0,{i}" for i in range(lt.MAX_DIFF_ROWS + 1))
    tasks, tally = lt.build([_row(ORIG.replace("60", "61"), diff=long)])
    assert tasks == [] and tally["refused-diff-too-long"] == 2


def test_weights_scale_the_train_cap_per_class():
    rows = [_row(ORIG.replace("60", "61"), rid=f"r{i}") for i in range(6)]
    tasks, _ = lt.build(rows, per_class_cap=2, weights={"const": 2.0})
    assert sum(1 for t in tasks if t["kind"] == repair_prompts.LOGIC_PREDICT_KIND) == 4
