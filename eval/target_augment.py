"""Accepted targets are ALL certificate-verified spellings, not just the generator's answer.

WHY THIS EXISTS
---------------
Compilation is many-to-one, so a task's target is an **equivalence class of sources**, and the
generator's answer is one member of it. `eval/repair_prompts.load_task_examples` trains on exactly
that one member as the completion, so a model that produces a DIFFERENT program in the same class is
penalised for the divergence by the cross-entropy term.

That is not hypothetical here. `evaluation-certified/ATTEMPTS.md` measured the certified run's exact
draws against the hidden answers: 22 of 32 are the answer modulo whitespace, and **10 are
substantively different programs that produce the identical object** -- one deleted the
`extern s32 syn_ext(s32);` declaration in favour of `typedef`/`s32_t` spellings, another moved a
trailing `return 0;` into a `default:` inside the switch. The oracle accepts all of them. The
objective should not be fighting the oracle.

WHAT THIS DOES
--------------
Proposes source-level rewrites of each *train-split* answer and keeps only the ones `certify_exact`
accepts. The safety net is the point: **a rewrite is never assumed to be neutral.** Every variant is
compiled and certified against the task's own target object, so an unsound rewrite is rejected on
evidence rather than on the author's confidence, and the failures are retained and reported.

SCOPE
-----
Train split ONLY, and deliberately. The frozen test split is the measurement; harvesting variants
there -- or harvesting the evaluation's own model outputs, which are test-split artifacts -- would
contaminate the panel it is measured on. A variant set is a training-data artifact and lives beside
the dataset, never inside the frozen manifest.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Every rewrite is a hypothesis about codegen neutrality. The certificate, not this table, decides.
REWRITES: dict[str, object] = {}


def rewrite(name):
    def register(function):
        REWRITES[name] = function
        return function
    return register


@rewrite("typedef-spelling")
def typedef_spelling(source: str) -> str | None:
    """Spell the fixed-width types through typedefs instead of the raw names.

    This is one of the two shapes the certified run's model actually produced. It is a pure
    type-spelling change, so it should be codegen-neutral -- and it is verified like every other.
    """
    if "typedef s32 s32_t;" in source or not re.search(r"\bs32\b", source):
        return None
    body = re.sub(r"\bs32\b", "s32_t", source)
    body = re.sub(r"\bu32\b", "u32_t", body)
    decls = "typedef int s32_t;\ntypedef unsigned int u32_t;\n"
    lines = body.splitlines(keepends=True)
    if not lines:
        return None
    # After the last include, so the typedefs follow the project headers.
    at = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#include"):
            at = index + 1
    lines.insert(at, decls)
    return "".join(lines)


@rewrite("explicit-s32-casts")
def explicit_casts(source: str) -> str | None:
    """Add an explicit `(s32)` to a returned arithmetic expression.

    The certified run's model did this on a `subtract-to-narrow` task. Calling something s32 that is
    already s32 is a no-op for codegen.
    """
    pattern = re.compile(r"(\n\s*return )(\()?([A-Za-z_0-9]+ [+\-^] [A-Za-z_0-9]+)(\))?;")

    def cast(match):
        return f"{match.group(1)}((s32){match.group(3)});"
    out, count = pattern.subn(cast, source)
    return out if count else None


@rewrite("hoist-default")
def hoist_default(source: str) -> str | None:
    """Move a trailing `return N;` after a switch INTO that switch as a `default:` arm.

    This is the other shape the certified run's model produced, on a `drop-switch-default` task: it
    restored the missing default in a different position. Same control flow, different text.

    THE GUARD MATTERS. The first version of this rewrite had none, so on a switch that already had a
    `default:` it emitted a second one -- `default: return 3398; default: return -1;` -- and a
    malformed closing brace. The compiler refused 8 of the 11 it fired on. That was a bug in the
    rewrite, not a fact about codegen, and `verified-targets.json` recorded it as a 27% yield until
    this was diagnosed. A rewrite that produces invalid C must be indistinguishable from one that
    produces different C: both are simply not accepted, and both are reported.
    """
    lines = source.splitlines(keepends=True)
    head = None
    for index, line in enumerate(lines):
        if re.match(r"\s*switch \(", line):
            head = index
            break
    if head is None:
        return None
    body_indent = lines[head][:len(lines[head]) - len(lines[head].lstrip())]

    close = None
    for index in range(head + 1, len(lines)):
        stripped = lines[index].strip()
        indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
        if stripped == "}" and indent == body_indent:
            close = index
            break
    if close is None:
        return None
    # Only when the switch has NO default of its own, which is the mutation this mirrors.
    if "default:" in "".join(lines[head + 1:close]):
        return None
    if close + 1 >= len(lines) or not re.match(r"\s*return [^;]+;\s*$", lines[close + 1]):
        return None
    tail = lines[close + 1].strip()
    return "".join(
        lines[:close]
        + [f"{body_indent}default:\n", f"{body_indent}    {tail}\n"]
        + [lines[close]]
        + lines[close + 2:])


@rewrite("blank-line-and-comment")
def blank_line_and_comment(source: str) -> str | None:
    """A comment and the blank line after it. Whitespace-only changes still prove the pipeline."""
    if "/* coverage variant */" in source:
        return None
    return source.replace("\n\n", "\n\n/* coverage variant */\n", 1)


def variants(source: str) -> list[tuple[str, str]]:
    """Every proposed spelling of one answer, by rewrite name. Unsound ones are filtered later."""
    out = []
    for name, function in REWRITES.items():
        try:
            result = function(source)
        except Exception:                                  # noqa: BLE001 - a bad rewrite is a no-op
            continue
        if result and result != source:
            out.append((name, result))
    return out


def compile_and_certify(repo: Path, resolved: dict, function: str, source: str,
                        work: Path, target_object: Path) -> dict:
    """Compile one spelling and ask the certificate whether it is the same object."""
    from eval.repair_dataset_synth import certify_exact, compile_unit
    work.mkdir(parents=True, exist_ok=True)
    run = compile_unit(repo, resolved, function, source, work)
    if not run["compiled"]:
        return {"compiled": False, "exact": False, "status": "compile-error",
                "stderr": (run.get("stderr") or "")[-300:]}
    verdict = certify_exact(target_object, work / f"{function}.o", source=source)
    return {"compiled": True, "exact": bool(verdict.get("exact")),
            "status": verdict.get("status"), "error": verdict.get("error")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", type=Path,
                    default=ROOT / "eval/results/local-posttraining-20260920/dataset/tasks.jsonl")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--split", default="train",
                    help="train only: the held-out split is the measurement, not a data source")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/local-posttraining-20260920/verified-targets.json")
    ap.add_argument("--variants-out", type=Path,
                    default=ROOT / "eval/results/local-posttraining-20260920/"
                                     "verified-target-variants.jsonl")
    args = ap.parse_args(argv)

    if args.split != "train":
        raise SystemExit("only the train split may be augmented; augmenting the held-out split "
                         "would change the panel the evaluation is measured on")

    from eval.repair_dataset_synth import DEFAULT_TARGET, resolve_recipe
    bundle = resolve_recipe(args.repo, DEFAULT_TARGET)
    resolved = bundle["provenance"]

    tasks = [json.loads(line) for line in
             args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    tasks = [t for t in tasks if t.get("split") == args.split
             and (t.get("child") or {}).get("exact")]
    print(json.dumps({"split": args.split, "tasks": len(tasks),
                      "rewrites": sorted(REWRITES)}), flush=True)

    work = ROOT / ".cache/target-augment"
    # Truncate BEFORE writing, not after: the first version unlinked at the end of the run, which
    # deleted every variant it had just appended. The result was a 0-row file and a receipt claiming
    # 82 accepted targets -- the counts came from memory while the artifact was empty.
    args.variants_out.parent.mkdir(parents=True, exist_ok=True)
    args.variants_out.write_text("", encoding="utf-8")
    per_rewrite: dict[str, dict] = {name: {"proposed": 0, "compiled": 0, "exact": 0}
                                    for name in REWRITES}
    rows, accepted_total = [], 0
    for task in tasks:
        answer = task["generator_source"]
        function = task["function"]      # noqa: F841 - kept for the compile call below
        target_dir = work / "target" / task["task_id"].replace(":", "_")
        from eval.repair_dataset_synth import compile_unit
        target_dir.mkdir(parents=True, exist_ok=True)
        built = compile_unit(args.repo, resolved, task["function"], answer, target_dir)
        if not built["compiled"]:
            rows.append({"task_id": task["task_id"], "error": "the answer itself did not compile"})
            continue
        target_object = target_dir / f"{task['function']}.o"

        accepted, rejected = [], []
        for name, text in variants(answer):
            per_rewrite[name]["proposed"] += 1
            case = work / "case" / task["task_id"].replace(":", "_") / name
            outcome = compile_and_certify(args.repo, resolved, task["function"], text,
                                          case, target_object)
            per_rewrite[name]["compiled"] += bool(outcome["compiled"])
            per_rewrite[name]["exact"] += bool(outcome["exact"])
            (accepted if outcome["exact"] else rejected).append(name)
            if outcome["exact"]:
                accepted_total += 1
                with args.variants_out.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "task_id": task["task_id"], "function": task["function"],
                        "rewrite": name, "source": text,
                        "certificate": {"status": outcome["status"]}}) + "\n")
        rows.append({"task_id": task["task_id"], "accepted": accepted, "rejected": rejected,
                     "accepted_count": len(accepted)})

    payload = {
        "split": args.split, "tasks": len(tasks),
        "accepted_targets_added": accepted_total,
        "tasks_with_at_least_one_extra_target": sum(1 for r in rows if r.get("accepted")),
        "per_rewrite": per_rewrite, "tasks_detail": rows,
        "note": ("Every accepted variant is certificate-exact against its own task's target. "
                 "Rejected variants are retained: a rewrite that is not codegen-neutral is a "
                 "finding, not a no-op."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # The receipt must agree with the artifact it points at, so count the file rather than trusting
    # the in-memory tally.
    written = sum(1 for line in args.variants_out.read_text(encoding="utf-8").splitlines()
                  if line.strip())
    if written != accepted_total:
        print(f"MISMATCH: {accepted_total} accepted in memory but {written} rows on disk",
              file=sys.stderr)
        return 1

    print(f"\naccepted extra verified targets: {accepted_total} across "
          f"{payload['tasks_with_at_least_one_extra_target']}/{len(tasks)} tasks")
    print(f"{'rewrite':24s} {'proposed':>9} {'compiled':>9} {'certified':>10} {'yield':>7}")
    for name, counts in per_rewrite.items():
        yield_pct = 100.0 * counts["exact"] / max(1, counts["proposed"])
        print(f"{name:24s} {counts['proposed']:9d} {counts['compiled']:9d} "
              f"{counts['exact']:10d} {yield_pct:6.1f}%")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
