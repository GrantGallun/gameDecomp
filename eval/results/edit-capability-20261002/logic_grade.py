"""Grade answers to compiler-logic tasks (eval/logic_tasks.py) -- explain answers by the compiler, not by string.

    python3 logic_grade.py TASKS.jsonl --self-check [--split exam] [--limit N]
    python3 logic_grade.py TASKS.jsonl --answers ANSWERS.jsonl     (rows: {"id": task id, "answer": text})

- logic-predict: `label` = the SAME/DIFFER line; `rows` = the changed instruction rows, compared as a multiset.
- logic-need: a withheld relevant fact must be answered `NEED: <its name>`; a withheld irrelevant fact must be answered
  normally (a NEED there is wrong: it asked for something that does not matter).
- logic-explain: the edit script is applied to the shown function and compiled ALONE in the task's checked context
  with the file's recipe (context_tasks.py); correct iff the function's listing equals the target's. Compilation is
  many-to-one, so a different correct edit scores as correct.

--self-check grades the dataset's own completions. Anything below 100% is a dataset or grader bug, not a model score.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from eval import logic_tasks as lt

CONTEXT_ROWS = Path.home() / "decomp/experiments/edit-capability-20261002/public/context.jsonl"


MARKUP = re.compile(r"\*\*|`|^\s*(?:[-*]\s+(?=[A-Z]))", re.M)


def normalize(text: str) -> str:
    """Lenient for every arm alike: drop code fences, bold and inline-code marks, and bullet markers, so a correct
    answer in markdown is not scored as wrong (a trained arm learns the bare format; base would lose on format)."""
    lines = [line for line in (text or "").splitlines() if not line.strip().startswith("```")]
    return MARKUP.sub("", "\n".join(lines)).strip()


def first_line(text: str) -> str:
    """Read explicit verdict lines, never a verdict word mentioned in explanatory prose."""
    text = normalize(text)
    verdicts = set()
    for line in text.splitlines():
        line = re.sub(r"^(?:(?:The |Final )?[Aa]nswer(?: is)?|Here)\s*:\s*", "", line.strip())
        m = re.fullmatch(r"(SAME|DIFFER|NEED:\s*\w+)[.!]?", line)
        if m:
            verdicts.add(re.sub(r"NEED:\s*", "NEED: ", m.group(1)))
    return next(iter(verdicts)) if len(verdicts) == 1 else ""


def row_key(row: str) -> str:
    """`- lw   v0,0(a0)` and `-lw v0,0(a0)` are the same row."""
    return row[0] + " ".join(row[1:].split())


def grade_predict(expected: str, answer: str) -> dict:
    e, a = first_line(expected), first_line(answer)
    label_ok = e.split(":")[0] == a.split(":")[0] if e.startswith("NEED") else e == a
    if e.startswith("NEED") or e == "SAME" or not label_ok:
        return {"label": label_ok, "rows": label_ok}
    rows = collections.Counter(row_key(r.strip()) for r in lt.diff_rows(normalize(answer)))
    return {"label": True, "rows": rows == collections.Counter(row_key(r.strip()) for r in lt.diff_rows(expected))}


def grade_need(expected: str, answer: str) -> dict:
    e, a = first_line(expected), first_line(answer)
    if e.startswith("NEED:"):
        return {"label": a.replace(" ", "") == e.replace(" ", ""), "rows": a.replace(" ", "") == e.replace(" ", "")}
    if a.startswith("NEED"):
        return {"label": False, "rows": False}           # asked for a fact the answer does not depend on
    return grade_predict(expected, answer)


def extract_script(answer: str) -> str:
    # Strip wrappers around a whole edit line, never operators inside its C payload.
    script = []
    for line in answer.splitlines():
        line = re.sub(r"^[-*]\s+(?=(?:REPLACE|DELETE|INSERT)\b)", "", line.strip())
        for wrapper in ("**", "`"):
            if line.startswith(wrapper) and line.endswith(wrapper):
                line = line[len(wrapper):-len(wrapper)]
        if lt.EDIT.match(line):
            script.append(line)
    return "\n".join(script)


def grade_explain(task: dict, answer: str, rows: dict, bmap: dict) -> dict:
    import public_plant as pp
    row = rows.get(task["row_id"])
    if row is None:
        return {"label": False, "rows": False, "error": "no context row"}
    script = extract_script(answer)
    fixed = lt.apply_script(row["perturbed_fn"], script) if script else None
    if fixed is None:
        return {"label": False, "rows": False, "error": "script does not parse"}
    obj = pp.compile_row(row, row["context"] + "\n" + fixed, "grade", bmap)
    ok = obj is not None and pp.listing(obj, row["function"]) == row["target"]
    return {"label": ok, "rows": ok, **({} if obj else {"error": "does not compile"})}


READ_MARKER = "/* ? */"


def read_statement(answer: str) -> str:
    """The C statement of a logic-read answer: code lines only, so a fenced or `Line 7:`-prefixed answer is not
    scored as wrong. Nothing is repaired: a missing semicolon stays missing and fails to compile."""
    # Not normalize(): its markdown pass deletes `**`, which is C (`(T **) p`). Only fences and whole-line wrappers go,
    # as in extract_script.
    out = []
    for line in (answer or "").splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        for wrapper in ("**", "`"):
            if len(line) > 2 * len(wrapper) and line.startswith(wrapper) and line.endswith(wrapper):
                line = line[len(wrapper):-len(wrapper)].strip()
        line = re.sub(r"^(?:Line \d+\s*:|\d+\|)\s*", "", line)
        if line:
            out.append(line)
    return " ".join(out)


def read_lines(answer: str) -> dict[int, str]:
    """`Line N: <statement>` answers of a multi-blank read task; the last answer for a line wins."""
    out = {}
    for line in (answer or "").splitlines():
        line = line.strip()
        for wrapper in ("**", "`"):
            if len(line) > 2 * len(wrapper) and line.startswith(wrapper) and line.endswith(wrapper):
                line = line[len(wrapper):-len(wrapper)].strip()
        m = re.match(r"^Line (\d+)\s*:\s*(.+)$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _fill(fn: str, answers: dict[int, str]) -> str | None:
    lines = fn.split("\n")
    for n, stmt in answers.items():
        if not (1 <= n <= len(lines)) or READ_MARKER not in lines[n - 1]:
            return None
        indent = lines[n - 1][:len(lines[n - 1]) - len(lines[n - 1].lstrip())]
        lines[n - 1] = indent + stmt
    return "\n".join(lines)


def grade_read_multi(row: dict, answer: str, bmap: dict) -> dict:
    """Full = every blank filled and the function compiles to the target. `lines_ok` = blanks whose answer ALONE (the
    other blanks left empty) reproduces the compiler's listing for that state: each claim checked on its own."""
    import public_plant as pp
    given = {n: s for n, s in read_lines(answer).items() if n in row["marker_lines"]}

    def compiles_to(fn, target):
        obj = pp.compile_row(row, row["context"] + "\n" + fn, "readm", bmap) if fn is not None else None
        return obj is not None and pp.listing(obj, row["function"]) == target

    lines_ok = sum(compiles_to(_fill(row["perturbed_fn"], {n: s}), row["partial_targets"][str(n)])
                   for n, s in given.items())
    full = len(given) == len(row["marker_lines"]) and compiles_to(_fill(row["perturbed_fn"], given), row["target"])
    return {"label": full, "rows": full, "lines_ok": lines_ok, "lines": len(row["marker_lines"])}


def grade_read(task: dict, answer: str, rows: dict, bmap: dict) -> dict:
    import public_plant as pp
    row = rows.get(task["row_id"])
    if row is None:
        return {"label": False, "rows": False, "error": "no context row"}
    if "marker_lines" in row:
        return grade_read_multi(row, answer, bmap)
    stmt = read_statement(answer)
    lines = row["perturbed_fn"].split("\n")
    k = row["marker_line"] - 1
    if not stmt or READ_MARKER not in lines[k]:
        return {"label": False, "rows": False, "error": "empty answer" if not stmt else "marker missing"}
    indent = lines[k][:len(lines[k]) - len(lines[k].lstrip())]
    fixed = "\n".join(lines[:k] + [indent + stmt] + lines[k + 1:])
    obj = pp.compile_row(row, row["context"] + "\n" + fixed, "read", bmap)
    ok = obj is not None and pp.listing(obj, row["function"]) == row["target"]
    return {"label": ok, "rows": ok, **({} if obj else {"error": "does not compile"})}


def decompiled_code(answer: str) -> str:
    """The first fenced block (```c or ```), else the whole answer."""
    m = re.search(r"```[A-Za-z]*\n(.*?)```", answer or "", re.S)
    return (m.group(1) if m else (answer or "")).strip()


def function_definition(code: str, name: str) -> str | None:
    """The definition of `name` alone (brace-matched), dropping includes, typedefs, structs and prototypes the
    answer added: the checked context already declares everything the function needs, and a redefinition breaks it."""
    for m in re.finditer(r"\b" + re.escape(name) + r"\s*\(", code):
        start = code.rfind("\n", 0, m.start()) + 1          # the line holding the return type and name
        brace = code.find("{", m.end())
        if brace < 0 or ";" in code[m.end():brace]:
            continue                                        # a prototype or a call, not the definition
        depth = 0
        for i in range(brace, len(code)):
            depth += {"{": 1, "}": -1}.get(code[i], 0)
            if depth == 0:
                return code[start:i + 1]
    return None


def tooled_code(code: str, name: str) -> str:
    """What the campaign's deterministic cleanup makes of an answer: solver.c89 (C99 types, attributes, register
    asm bindings, inline, declaration hoisting; static dropped from the target) on the target definition alone."""
    from solver import c89
    fn = function_definition(code, name) or code
    return c89.public_definition(c89.to_c89(fn), name)


def _compile_grade(row: dict, code: str, bmap: dict, tag: str) -> dict:
    import public_plant as pp
    obj = pp.compile_row(row, row["context"] + "\n" + code, tag, bmap) if code else None
    got = pp.listing(obj, row["function"]) if obj else None
    if got is None:
        return {"compiles": False, "exact": False, "distance": None}
    return {"compiles": True, "exact": got == row["target"],
            "distance": len(lt.diff_rows(pp.mine.gnu_diff(row["target"], got)))}


def grade_decompile(task: dict, answer: str, rows: dict, bmap: dict) -> dict:
    """label = the RAW answer compiles in the checked context; rows = raw exact; distance = instruction rows still
    different (None when it does not compile). `tooled` = the same after the campaign's cleanup (tooled_code): raw
    measures the model (a tool can hide a model forgetting C89), tooled what the pipeline would get."""
    row = rows.get(task["row_id"])
    if row is None:
        return {"label": False, "rows": False, "error": "no context row"}
    code = decompiled_code(answer)
    raw = _compile_grade(row, code, bmap, "decomp")
    tooled = _compile_grade(row, tooled_code(code, row["function"]), bmap, "decompt") if code else raw
    return {"label": raw["compiles"], "rows": raw["exact"], "compiles": raw["compiles"], "distance": raw["distance"],
            "target_rows": len(row["target"]), "tooled": tooled}


def grade(task: dict, answer: str, rows: dict, bmap: dict) -> dict:
    kind = task["kind"]
    if kind == "decompile":
        return grade_decompile(task, answer, rows, bmap)
    if kind == "logic-read":
        return grade_read(task, answer, rows, bmap)
    if kind == "logic-explain":
        return grade_explain(task, answer, rows, bmap)
    if kind == "logic-need":
        return grade_need(task["completion"], answer)
    return grade_predict(task["completion"], answer)


def validate_answers(tasks: list[dict], answers: list[dict]) -> dict[str, str]:
    ids = [t["id"] for t in tasks]
    answer_ids = [a["id"] for a in answers]
    if len(ids) != len(set(ids)) or len(answer_ids) != len(set(answer_ids)):
        raise ValueError("duplicate task or answer id")
    missing = set(ids) - set(answer_ids)
    if missing:
        raise ValueError(f"incomplete exam: {len(missing)} missing answers")
    errors = [a["id"] for a in answers if a["id"] in ids and "error" in a]
    if errors:
        raise ValueError(f"incomplete exam: {len(errors)} inference errors")
    return {a["id"]: a["answer"] for a in answers}


def main():
    import public_plant as pp
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("--answers")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--split", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--context", default=str(CONTEXT_ROWS))
    ap.add_argument("--out", help="per-task grades (.jsonl), for paired arm comparisons")
    ap.add_argument("--exam", help="a frozen exam (eval/logic_exam.py): grade exactly its ids")
    a = ap.parse_args()
    data = Path(a.tasks).read_bytes()
    tasks = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    if a.exam:
        exam = json.load(open(a.exam))
        if hashlib.sha256(data).hexdigest() != exam["tasks_sha256"]:
            raise ValueError("tasks changed after exam freeze")
        ids = set(exam["ids"])
        tasks = [t for t in tasks if t["id"] in ids]
        if {t["id"] for t in tasks} != ids:
            raise ValueError("frozen exam names missing task ids")
    if a.split:
        tasks = [t for t in tasks if t["split"] == a.split]
    if a.limit:
        tasks = tasks[:a.limit]
    if not tasks:
        raise ValueError("cannot grade an empty task set")
    if a.self_check:
        answers = {t["id"]: t["completion"] for t in tasks}
    else:
        answers = validate_answers(tasks, list(map(json.loads, open(a.answers))))
    rows = {r["id"]: r for r in map(json.loads, open(a.context))}
    bmap = pp.builds()
    by = collections.defaultdict(collections.Counter)
    failures = []
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        graded = list(ex.map(lambda t: (t, grade(t, answers.get(t["id"], ""), rows, bmap)), tasks))
        for t, g in graded:
            variant = t["variant"].split(":")[0]
            if t["kind"] == "logic-need":          # relevant (answer NEED) and control (answer normally) apart
                variant = "relevant" if t["completion"].startswith("NEED:") else "control"
            key = (t["split"], t["kind"], variant)
            by[key]["n"] += 1
            by[key]["label"] += g["label"]
            by[key]["rows"] += g["rows"]
            if a.self_check and not g["rows"]:
                failures.append((t["id"], g.get("error", "")))
    for (split, kind, variant), c in sorted(by.items()):
        print(f"{split:6} {kind:14} {variant:9} n {c['n']:5}  label {c['label']:5}  full {c['rows']:5}")
    if a.out:
        with open(a.out, "w") as f:
            for t, g in graded:
                f.write(json.dumps({"id": t["id"], "split": t["split"], "kind": t["kind"],
                                    "variant": "relevant" if t["completion"].startswith("NEED:") else
                                    ("control" if t["kind"] == "logic-need" else t["variant"].split(":")[0]),
                                    **g}) + "\n")
    if a.self_check:
        print(f"self-check failures: {len(failures)}")
        for f in failures[:15]:
            print("  ", *f)
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
