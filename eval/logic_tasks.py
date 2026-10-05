"""Compiler-logic training tasks from compiler-labelled C pairs, weighted toward the holes.

    python3 -m eval.logic_tasks DIFFER.jsonl [SAME.jsonl ...] --out DIR [--holdout-repo sm64]
                                [--per-class-cap 1200] [--weights holes.json]

Input rows are planted pairs (eval/results/edit-capability-20261002/public_plant.py): `original_def`, `perturbed_def`,
`label` ("differ" with the compiler's instruction `diff`, or "same": the compiler erased the edit), `class`, `opt`,
`split`, `split_group`, `repository`. Every label was produced by compiling both spellings; none is a guess.

Two task kinds (prompts built only in eval/repair_prompts.py):
- logic-predict: two spellings -> SAME, or DIFFER + the instruction rows that change. The forward compiler rule.
  Built from both labels; A/B order is chosen by hash so neither side is always the original.
- logic-explain: a function + its instruction diff against the target -> an edit script that restores the target.
  Built from "differ" rows only. The script is checkable by the oracle (`apply_script`, then compile): grading an
  answer must compile it, because compilation is many-to-one and a different edit can be equally right.

Splits: the corpus's own file-grouped split decides train vs `exam`; `--holdout-repo` sends a whole repository to
`check` (SM64 source is probably in the base model's pretraining, so it is where recall would show up as skill).
Hole targeting: `--weights {class: w}` scales each class's train CAP (a sampling ceiling, not a loss weight: raising
the cap of a class that is already below it changes nothing). The intended source of the weights is the base model's
per-class error on `exam`, which makes `exam` a development set, not a held-out one.

Context (v2): rows must carry `context`, `original_fn`, `perturbed_fn` from context_tasks.py, whose standalone compile
reproduced the full-file instructions; rows without them are refused (v1 prompts showed only the function, and some
labels were underdetermined: `x / 2` vs `x >> 1` flips with a hidden typedef's signedness; audit 2026-10-03).
Counterfactual facts add a TWIN predict task (same pair, one declaration retyped, the compiler's new answer) and
logic-need (that declaration shown as `??`: `NEED: <name>` when the fact changes the answer, the usual answer when not).
"""
from __future__ import annotations

import argparse
import collections
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

from eval import repair_prompts

COMPILER = "IDO 5.3"
MAX_DIFF_ROWS = 40


class Leak(ValueError):
    pass


class NoContext(ValueError):
    """A row without a sufficiency-checked context (context_tasks.py): its label may be underdetermined by the
    prompt (audit 2026-10-03), so it is refused, not trained on."""


def diff_rows(diff: str) -> list[str]:
    """Instruction rows of a unified diff, without file headers and hunk markers."""
    return [r for r in (diff or "").splitlines()
            if r.startswith(("-", "+")) and not r.startswith(("---", "+++"))]


class TooLong(ValueError):
    pass


def _cap(rows: list[str]) -> str:
    # Refused, not truncated: a cut diff hides the evidence the answer depends on (audit 2026-10-03: 1,592 train
    # prompts carried a truncation marker).
    if len(rows) > MAX_DIFF_ROWS:
        raise TooLong(f"{len(rows)} diff rows")
    return "\n".join(rows)


def _flip(rows: list[str]) -> list[str]:
    return [("+" if r[0] == "-" else "-") + r[1:] for r in rows]


def marked(text: str, other: str) -> str:
    """`text` with `>> ` on each line that is not in the same position of `other` (difflib alignment)."""
    a, b = text.split("\n"), other.split("\n")
    changed = set()
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag != "equal":
            changed.update(range(i1, max(i2, i1 + 1)) if tag != "insert" else [max(0, i1 - 1)])
    return "\n".join((">> " if i in changed else "   ") + line for i, line in enumerate(a))


def numbered(text: str) -> str:
    return "\n".join(f"{i:3d}| {line}" for i, line in enumerate(text.split("\n"), 1))


def edit_script(current: str, target: str) -> list[str]:
    """Edits, numbered against `current`, that turn it into `target`."""
    a, b = current.split("\n"), target.split("\n")
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            out += [f"REPLACE {i1 + k + 1}: {b[j1 + k]}" for k in range(i2 - i1)]
            continue
        if tag in ("delete", "replace"):
            out += [f"DELETE {n}" for n in range(i1 + 1, i2 + 1)]
        if tag in ("insert", "replace"):
            out += [f"INSERT AFTER {i1}: {b[j]}" for j in range(j1, j2)]
    return out


EDIT = re.compile(r"^(REPLACE (\d+)|INSERT AFTER (\d+)|DELETE (\d+))(?::(?: (.*))?)?$")


def apply_script(current: str, script: str) -> str | None:
    """Apply an edit script (numbers refer to `current` as shown). None when a line does not parse or is out of range."""
    lines = current.split("\n")
    replace, delete, insert = {}, set(), collections.defaultdict(list)
    for raw in script.strip().splitlines():
        m = EDIT.match(raw.rstrip())
        if not m:
            return None
        if m.group(2):
            n = int(m.group(2))
            if not 1 <= n <= len(lines):
                return None
            replace[n] = m.group(5) or ""
        elif m.group(3) is not None:
            n = int(m.group(3))
            if not 0 <= n <= len(lines):
                return None
            insert[n].append(m.group(5) or "")
        else:
            n = int(m.group(4))
            if not 1 <= n <= len(lines):
                return None
            delete.add(n)
    out = list(insert.get(0, []))
    for n, line in enumerate(lines, 1):
        if n not in delete:
            out.append(replace.get(n, line))
        out.extend(insert.get(n, []))
    return "\n".join(out)


def _h(*parts) -> int:
    return int(hashlib.sha256("\x00".join(map(str, parts)).encode()).hexdigest(), 16)


def _predict(rid: str, opt: str, context: str, orig: str, pert: str, label: str, diff: str,
             withheld: bool = False, completion: str | None = None,
             type_options: tuple[str, str] | None = None) -> dict:
    """`diff` is oriented original (-) -> perturbed (+); A/B order is chosen by hash and the rows follow it."""
    swap = _h("ab", rid) % 2 == 1
    a, b = (pert, orig) if swap else (orig, pert)
    prompt = repair_prompts.logic_predict_prompt(compiler=COMPILER, opt=opt or "-O2", context=context,
                                                 a=marked(a, b), b=marked(b, a), withheld=withheld,
                                                 type_options=type_options)
    if completion is None:
        rows = diff_rows(diff)
        completion = "SAME" if label == "same" else "DIFFER\n" + _cap(_flip(rows) if swap else rows)
    return {"kind": repair_prompts.LOGIC_NEED_KIND if withheld else repair_prompts.LOGIC_PREDICT_KIND,
            "prompt": prompt, "completion": completion}


def _require_context(row: dict) -> None:
    if not row.get("context") or not row.get("original_fn") or not row.get("perturbed_fn"):
        raise NoContext(row.get("id"))


def predict_task(row: dict) -> dict:
    _require_context(row)
    return _predict(row["id"], row.get("opt"), row["context"], row["original_fn"], row["perturbed_fn"],
                    row["label"], row["diff"])


def explain_task(row: dict) -> dict:
    _require_context(row)
    current, target = row["perturbed_fn"], row["original_fn"]
    script = edit_script(current, target)
    if apply_script(current, "\n".join(script)) != target:
        raise ValueError(f"edit script does not reproduce the target for {row['id']}")
    # The diff is stored target(-) vs current(+), which is what the template states.
    prompt = repair_prompts.logic_explain_prompt(compiler=row.get("compiler") or COMPILER, opt=row.get("opt") or "-O2",
                                                 context=row["context"], numbered=numbered(current),
                                                 diff=_cap(diff_rows(row["diff"])))
    present = {line.strip() for line in current.split("\n")}
    for line in target.split("\n"):
        stripped = line.strip()
        if len(stripped) > 6 and stripped not in present and stripped in prompt:
            raise Leak(f"answer line in the prompt of {row['id']}: {stripped}")
    return {"kind": repair_prompts.LOGIC_EXPLAIN_KIND, "prompt": prompt, "completion": "\n".join(script)}


def withhold(context: str, mutated: str, old: str) -> str | None:
    """`context` with the one declaration `mutated` retypes shown as `??`. None unless exactly one line differs and
    the old type is found on it."""
    a, b = context.split("\n"), mutated.split("\n")
    if len(a) != len(b):
        return None
    changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(changed) != 1:
        return None
    line = a[changed[0]]
    new = re.sub(rf"(?<![\w]){re.escape(old)}(?![\w])", "??", line, count=1)
    if new == line:
        return None
    a[changed[0]] = new
    return "\n".join(a)


def fact_tasks(row: dict) -> list[tuple[str, dict]]:
    """From the row's counterfactual facts (context_tasks.py): a TWIN for a fact that changes the answer (same visible
    pair, other context, the compiler's other answer), and a WITHHELD-fact task: `NEED: <name>` when that fact
    changes the answer, the ordinary answer when it does not (the control that keeps NEED from being a default)."""
    _require_context(row)
    out, need_done, control_done = [], False, False
    for f in row.get("facts") or []:
        relevant = f["label_flips"] or f["rows_change"]
        tag = f"{f['name']}:{f['kind']}"
        if relevant:
            try:
                out.append((f"twin:{tag}", _predict(f"{row['id']}:twin:{tag}", row.get("opt"), f["context"],
                                                    row["original_fn"], row["perturbed_fn"], f["label"], f["diff"])))
            except TooLong:
                pass
        if (relevant and need_done) or (not relevant and control_done):
            continue
        shown = withhold(row["context"], f["context"], f["old"])
        if shown is None:
            continue
        completion = f"NEED: {f['name']}" if relevant else None
        try:
            out.append((f"need:{tag}", _predict(f"{row['id']}:need:{tag}", row.get("opt"), shown, row["original_fn"],
                                                row["perturbed_fn"], row["label"], row["diff"], withheld=True,
                                                completion=completion, type_options=(f["old"], f["new"]))))
        except TooLong:
            continue
        need_done, control_done = need_done or relevant, control_done or not relevant
    return out


SPLITS = {"train": "train", "dev": "exam"}


class UnknownSplit(ValueError):
    pass


def split_of(row: dict, holdout_repos: set[str]) -> str:
    """Explicit map only. Every other label (test, heldout, check, missing) is refused: the importer used to send
    anything that was not `dev` to train (audit 2026-10-03), which fails open on a held-out row."""
    if row.get("split") not in SPLITS:
        raise UnknownSplit(f"split {row.get('split')!r} of {row.get('id')}")
    if row.get("repository") in holdout_repos:
        return "check"
    return SPLITS[row["split"]]


def build(rows: list[dict], *, holdout_repos=(), per_class_cap: int = 1200, weights: dict | None = None):
    holdout_repos = set(holdout_repos)
    tally = collections.Counter()
    out = []
    ids = collections.Counter(r["id"] for r in rows)
    duplicated = sorted(i for i, n in ids.items() if n > 1)
    if duplicated:
        raise ValueError(f"{len(duplicated)} input ids occur more than once, e.g. {duplicated[:3]}: "
                         "ids must name one source pair")
    for row in sorted(rows, key=lambda r: _h("order", r["id"])):
        if row.get("label") not in ("same", "differ"):
            tally["refused-no-label"] += 1          # a label is the compiler's verdict; never inferred here
            continue
        split = split_of(row, holdout_repos)
        makers = [("", predict_task)] + ([("", explain_task)] if row.get("label") == "differ" else [])
        try:
            makers += [(suffix, (lambda r, t=task: t)) for suffix, task in fact_tasks(row)]
        except NoContext:
            pass                                   # counted once below, by predict_task
        except TooLong:
            tally["refused-diff-too-long"] += 1
        for suffix, make in makers:
            try:
                task = make(row)
            except NoContext:
                tally["refused-no-context"] += 1
                continue
            except Leak:
                tally["refused-leak"] += 1
                continue
            except TooLong:
                tally["refused-diff-too-long"] += 1
                continue
            except ValueError:
                tally["refused-script"] += 1
                continue
            key = (split, task["kind"], row["class"], row.get("label"))
            cap = per_class_cap * (weights or {}).get(row["class"], 1.0) if split == "train" else None
            if cap is not None and tally[key] >= cap:
                tally["capped"] += 1
                continue
            tally[key] += 1
            out.append(task | {"id": f"{task['kind']}:{row['id']}" + (f":{suffix}" if suffix else ""),
                               "row_id": row["id"], "variant": suffix or "base",
                               "class": row["class"], "label": row.get("label"),
                               "split": split, "split_group": row.get("split_group"),
                               "repository": row.get("repository"), "function": row.get("function"),
                               "provenance": row.get("source_kind") or "",
                               "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION})
    return out, tally


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--holdout-repo", action="append", default=[])
    ap.add_argument("--per-class-cap", type=int, default=1200)
    ap.add_argument("--weights")
    a = ap.parse_args(argv)
    rows = [json.loads(line) for p in a.inputs for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    weights = json.loads(Path(a.weights).read_text()) if a.weights else None
    tasks, tally = build(rows, holdout_repos=a.holdout_repo, per_class_cap=a.per_class_cap, weights=weights)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(t) + "\n" for t in tasks).encode("utf-8")
    (out / "tasks.jsonl").write_bytes(data)
    counts = collections.Counter((t["split"], t["kind"]) for t in tasks)
    manifest = {"schema_version": 1, "inputs": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in a.inputs},
                "tasks_sha256": hashlib.sha256(data).hexdigest(), "tasks": len(tasks),
                "by_split_kind": {f"{s}/{k}": n for (s, k), n in sorted(counts.items())},
                "by_class": dict(collections.Counter(f"{t['split']}/{t['class']}/{t['label']}" for t in tasks)),
                "refused": {k: v for k, v in tally.items() if isinstance(k, str)},
                "holdout_repos": a.holdout_repo, "per_class_cap": a.per_class_cap, "weights": weights,
                "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(json.dumps({k: manifest[k] for k in ("tasks", "by_split_kind", "refused")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
