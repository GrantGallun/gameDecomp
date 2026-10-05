"""Regression exam of GENERAL work: decompile a held-out function from its instructions.

    python3 decompile_tasks.py [--context public/context-v3.jsonl] [--out-rows public/decompile-v1.jsonl]
                               [--out-tasks DIR] [--max-rows 80]

Every targeted pilot (logic, reading, RL) trains on narrow, planted edits. Narrow training can pull a model away from
work it could already do (the logic pilot's trained arms lost twin-label accuracy; RL collapsed NEED), and none of the
targeted exams would show it. This exam is the work everything else rests on: the declarations, the function's
signature and its compiled instructions in, the whole function out. Graded by compiling the answer alone in its
checked context (logic_grade.grade_decompile): exact, compiles, and the instruction rows still different.

Functions come only from held-out splits (dev -> exam, SM64 -> check), one task per function, never trained on.
The answer key (the original function) is used only by --self-check; the prompt shows the signature and the
instructions, never the body.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import public_plant as pp
from eval import logic_tasks as lt
from eval import repair_prompts

HOLDOUT = {"sm64"}


def signature(fn: str) -> str | None:
    head = fn.split("{", 1)[0].strip()
    return " ".join(head.split()) if head and "(" in head else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--context", type=Path, default=pp.OUT / "context-v3.jsonl")
    ap.add_argument("--out-rows", type=Path, default=pp.OUT / "decompile-v1.jsonl")
    ap.add_argument("--out-tasks", type=Path, default=pp.OUT / "decompile-v1")
    ap.add_argument("--max-rows", type=int, default=80)
    a = ap.parse_args(argv)
    if a.out_rows.exists():
        raise SystemExit(f"{a.out_rows} exists: generated rows are never overwritten")
    seen, rows, tasks, tally = set(), [], [], collections.Counter()
    for line in open(a.context):
        r = json.loads(line)
        key = (r["repository"], r["variant"], r["file"], r["function"])
        if key in seen or not (r.get("context") and r.get("original_fn") and r.get("target")):
            continue
        seen.add(key)
        split = lt.split_of(r, HOLDOUT)
        if split == "train":
            continue
        if len(r["target"]) > a.max_rows:
            tally["too-many-rows"] += 1
            continue
        sig = signature(r["original_fn"])
        if sig is None:
            tally["no-signature"] += 1
            continue
        rid = f"decompile:{r['id'].split(':', 2)[2]}"
        row = {"id": rid, "class": "decompile", "label": "differ", "source_kind": "public-decompile",
               **{k: r.get(k) for k in ("repository", "variant", "file", "function", "split", "split_group", "opt")},
               "context": r["context"], "original_fn": r["original_fn"], "target": r["target"]}
        prompt = repair_prompts.decompile_prompt(compiler=lt.COMPILER, opt=r.get("opt") or "-O2",
                                                 context=r["context"], signature=sig,
                                                 listing="\n".join(r["target"]))
        body = [s.strip() for s in r["original_fn"].split("\n")[1:] if len(s.strip()) > 6]
        if any(s in prompt.replace(r["context"], "") for s in body if s not in ("{", "}")):
            tally["refused-leak"] += 1
            continue
        rows.append(row)
        tasks.append({"id": rid, "row_id": rid, "kind": repair_prompts.DECOMPILE_KIND, "prompt": prompt,
                      "completion": r["original_fn"], "variant": "base", "class": "decompile", "label": "differ",
                      "split": split, "split_group": r.get("split_group"), "repository": r["repository"],
                      "function": r["function"], "provenance": "public-decompile",
                      "prompt_version": repair_prompts.LOGIC_PROMPT_VERSION})
        tally[f"{split}/decompile"] += 1
    a.out_rows.write_text("".join(json.dumps(r) + "\n" for r in rows))
    a.out_tasks.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(t) + "\n" for t in tasks).encode()
    (a.out_tasks / "tasks.jsonl").write_bytes(data)
    manifest = {"tasks": len(tasks), "tally": dict(tally), "tasks_sha256": hashlib.sha256(data).hexdigest(),
                "rows_sha256": hashlib.sha256(a.out_rows.read_bytes()).hexdigest(), "context": str(a.context)}
    (a.out_tasks / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
