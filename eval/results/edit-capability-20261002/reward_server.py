"""Reward and feedback service for eval/train_grpo.py and eval/logic_exam.py: the trusted grader in its own process.

    python3 reward_server.py TASKS.jsonl [--context CONTEXT.jsonl] [--splits train]
    stdin:  {"id": task id, "answer": text}  one per line
    stdout: {"id": ..., "reward": float, "label": bool, "full": bool, "feedback": text}

Rewards come only from logic_grade (explain answers are applied and COMPILED in their checked context; predict/need
answers are compared with compiler-produced labels). Reward: full answer 1.0; correct SAME/DIFFER/NEED verdict with
wrong rows 0.3 (predict/need only); otherwise 0.

`feedback` (explain tasks) is what the compiler says about the answer -- the edit script did not parse / the edited
function does not compile / the instruction rows that still differ from the target -- the same evidence the solver's
repair loop gets after an attempt. It never contains the target source.

Only `--splits` tasks are served (default train): training rewards can never come from evaluation items. The exam
runner starts its own instance with `--splits exam,check` for multi-turn FEEDBACK only; its grades still come from
logic_grade over the final answers.
"""
from __future__ import annotations

import argparse
import json
import sys

import logic_grade as lg
import public_plant as pp

from eval import logic_tasks as lt


NOT_COMPILING = 10 ** 6       # distance of an answer that does not parse or compile: worse than any compiled one


FEEDBACK_V2 = False      # set by --feedback-v2: verbatim compiler errors. v1 (default) keeps running comparisons fixed.


def explain_feedback(task: dict, answer: str, rows: dict, bmap: dict) -> tuple[str, int]:
    """(feedback text, distance). Distance = instruction rows that still differ (0 = exact): what keep-best ranks."""
    import re
    row = rows.get(task["row_id"])
    if row is None:
        return "no context row", NOT_COMPILING
    # extract_script, as the grade uses: normalize() deleted `**` (C double pointers) from feedback scripts until
    # 2026-10-04, so feedback and keep-best distance could disagree with the final grade on such answers.
    script = lg.extract_script(answer)
    fixed = lt.apply_script(row["perturbed_fn"], script) if script else None
    if fixed is None:
        return ("Your edit script did not parse or names a line that does not exist. Use only lines of the form "
                "REPLACE n: <text> | INSERT AFTER n: <text> | DELETE n, numbered as shown."), NOT_COMPILING
    obj, messages = pp.compile_row_err(row, row["context"] + "\n" + fixed, "feedback", bmap)
    if obj is None and not FEEDBACK_V2:
        return "The edited function does not compile.", NOT_COMPILING
    if obj is None:
        # The compiler's own words, with file lines re-based to the function's numbering as shown.
        offset = (row["context"] + "\n").count("\n")
        messages = re.sub(r"line (\d+)", lambda m: f"line {int(m.group(1)) - offset}"
                          if int(m.group(1)) > offset else "a context line", messages)
        return "The edited function does not compile. Compiler messages:\n" + messages.strip()[-1200:], NOT_COMPILING
    listing = pp.listing(obj, row["function"])
    if listing == row["target"]:
        return "The edited function compiles to the target exactly.", 0
    rows_left = lt.diff_rows(pp.mine.gnu_diff(row["target"], listing or []))
    return ("The edited function compiles but still differs from the target. Target rows (-) vs your rows (+):\n"
            + "\n".join(rows_left[:lt.MAX_DIFF_ROWS])), len(rows_left)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--context", default=str(lg.CONTEXT_ROWS))
    ap.add_argument("--splits", default="train")
    ap.add_argument("--feedback-v2", action="store_true", help="verbatim compiler errors in feedback")
    a = ap.parse_args()
    global FEEDBACK_V2
    FEEDBACK_V2 = a.feedback_v2
    splits = set(a.splits.split(","))
    tasks = {t["id"]: t for t in map(json.loads, open(a.tasks)) if t["split"] in splits}
    rows = {r["id"]: r for r in map(json.loads, open(a.context))}
    bmap = pp.builds()
    print(json.dumps({"ready": True, "train_tasks": len(tasks), "splits": sorted(splits)}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        task = tasks.get(req["id"])
        if task is None:
            print(json.dumps({"id": req["id"], "error": f"not a {'/'.join(sorted(splits))} task"}), flush=True)
            continue
        try:
            g = lg.grade(task, req.get("answer") or "", rows, bmap)
            feedback, distance = (explain_feedback(task, req.get("answer") or "", rows, bmap)
                                  if task["kind"] == "logic-explain" and req.get("feedback") else ("", None))
        except Exception as exc:                  # a grader failure is an error, never a zero reward
            print(json.dumps({"id": req["id"], "error": f"{type(exc).__name__}: {exc}"}), flush=True)
            continue
        full, label = bool(g["rows"]), bool(g["label"])
        partial = 0.3 if (label and not full and task["kind"] != "logic-explain") else 0.0
        print(json.dumps({"id": req["id"], "reward": 1.0 if full else partial, "label": label, "full": full,
                          "feedback": feedback, "distance": distance}), flush=True)


if __name__ == "__main__":
    main()
