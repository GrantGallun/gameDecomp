"""Deterministic inverse repair: undo the mutation WITHOUT a model and WITHOUT the answer.

WHY THIS EXISTS
---------------
`eval/repair_mutations.py` damages the generator's source with a KNOWN, deterministic rewrite. Six of
its seven mutations are mechanically invertible, and the inverse uses only the text the solver was
already given -- the candidate in the prompt. So a pass like this spends **compiler calls and zero
model calls**, which is exactly what "improve certified matches within a fixed inference budget"
asks for: a deterministic repair is free budget.

This is the same discipline as `solver/diffrepair.py` ("repair struct layout from the ORACLE'S OWN
DIFF, not from the evidence tier"), applied to the synthetic curriculum, whose mutations are known by
construction.

WHAT IT MAY AND MAY NOT READ
----------------------------
It reads `input.candidate` and the task's target object. It never reads `generator_source` to BUILD a
repair; the recorded answer is consulted only afterwards, to report how often a deterministic repair
happened to reproduce it, which is a diagnostic and cannot influence the result.

WHAT IT DOES NOT CLAIM
----------------------
- A repair that certifies is a certified match, exactly like a model's would be. It is not "cheating":
  the mutation catalogue is the curriculum's own definition of the defect.
- It does NOT mean the model's 4 -> 19 is wrong. It means part of what that number measures is the
  invertibility of a fixed mutation, and that has to be said out loud rather than discovered later.
- `drop-switch-default` is NOT invertible from the source: the removed arm's return value is gone.
  It declines and is reported as declining, rather than guessing a constant.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each inverse is paired with the mutation in `eval/repair_mutations.py` that it undoes, so the
# catalogue cannot drift away from the curriculum it is inverting.
INVERSES: dict[str, object] = {}
# inverse name -> the mutation it undoes. Usually the same string, but a mutation can have more than
# one inverse spelling (see `subtract-to-narrow-unparenthesised`), and the pairing has to stay
# explicit or the catalogue silently stops covering the curriculum.
INVERSE_MUTATION: dict[str, str] = {}


def inverse(name, mutation=None):
    def register(function):
        INVERSES[name] = function
        INVERSE_MUTATION[name] = mutation or name
        return function
    return register


@inverse("narrow-locals")
def undo_widen_locals(source: str) -> str | None:
    """`s16 x;` -> `s32 x;`. Inverts `widen_locals`, which narrowed the first two s32 locals."""
    out, count = re.subn(r"\bs16 (\w+);", r"s32 \1;", source)
    return out if count else None


@inverse("split-initialiser")
def undo_split_initialiser(source: str) -> str | None:
    """Drop the `tmp_<var>` temporary that `split_initialiser` introduced.

    Inverts the exact shape it emits: an extra `s32 tmp_v;` plus `tmp_v = p * k;` and `v = tmp_v;`.
    """
    pattern = re.compile(
        r"\n    s32 tmp_(?P<var>\w+);\n\n    tmp_\w+ = (?P<expr>[^;]+);\n    (?P<var2>\w+) = tmp_\w+;")
    match = pattern.search(source)
    if not match:
        return None
    var, expr = match.group("var"), match.group("expr").strip()
    if match.group("var2") != var:
        return None
    return source[:match.start()] + f"\n    {var} = {expr};" + source[match.end():]


@inverse("subtract-to-narrow")
def undo_subtract_to_narrow(source: str) -> str | None:
    """`(s16)(a - b)` -> `(a - b)`."""
    out, count = re.subn(r"\(s16\)\((?P<inner>[^()]*(?:\([^()]*\))?[^()]*)\)",
                         lambda m: f"({m.group('inner').strip()})", source)
    return out if count else None


@inverse("subtract-to-narrow-unparenthesised", mutation="subtract-to-narrow")
def undo_subtract_to_narrow_bare(source: str) -> str | None:
    """`(s16)(a - b)` -> `a - b`: the same inverse WITHOUT re-adding the parentheses.

    The mutation wraps an expression that may not have been parenthesised, and the inverse cannot
    know which. Emitting both spellings costs one extra compile and makes the byte-exact recovery
    reachable, which the parenthesised form alone was not: on the frozen panel this inverse
    certified 2/2 while reproducing the recorded source 0/2.
    """
    out, count = re.subn(r"\(s16\)\((?P<inner>[^()]*(?:\([^()]*\))?[^()]*)\)",
                         lambda m: m.group("inner").strip(), source)
    return out if count else None


@inverse("while-form")
def undo_while_form(source: str) -> str | None:
    """`i = a; while (c) { B; s; }` -> `for (i = a; c; s) { B }`.

    Inverts `while_form`, which split a for-head into an assignment plus a while. Only the shape it
    emits is matched: an assignment statement immediately followed by a while whose body ends in a
    bare `s;` line.
    """
    pattern = re.compile(
        r"(?P<indent>[ ]*)(?P<var>\w+) = (?P<init>[^;]+);\n"
        r"(?P=indent)while \((?P<cond>[^)]+)\) \{\n(?P<body>.*?)\n"
        r"(?P=indent)    (?P<step>[^;{}]+);\n(?P=indent)\}", re.S)
    match = pattern.search(source)
    if not match:
        return None
    indent = match.group("indent")
    body = match.group("body")
    step = match.group("step").strip()
    # No extra "is the step last?" check: the regex already requires the step to be the final
    # statement before the closing brace, and a check comparing the last line of the NON-GREEDY
    # `body` capture against the step compares two different things (`g(i);` vs `i++`) and declined
    # on every real candidate. `body` is already the loop body WITHOUT the step, which is what the
    # for-head needs -- keeping it would emit the increment twice, a different program.
    head = (f"{indent}for ({match.group('var')} = {match.group('init').strip()}; "
            f"{match.group('cond').strip()}; {step}) {{\n"
            f"{body}\n{indent}}}")
    return source[:match.start()] + head + source[match.end():]


@inverse("divide-to-shift")
def undo_divide_to_shift(source: str) -> str | None:
    """`(x >> n)` -> `(x / 2**n)`. Inverts `divide_to_shift`; it never fires today, and is kept
    paired with its mutation so the catalogue stays complete rather than silently partial."""
    pattern = re.compile(r"\((?P<var>\w+) >> (?P<shift>\d+)\)")

    def restore(match):
        return f"({match.group('var')} / {1 << int(match.group('shift'))})"
    out, count = pattern.subn(restore, source)
    return out if count else None


@inverse("invert-comparison")
def undo_invert_comparison(source: str) -> str | None:
    """`return b < a ? x : y;` -> `return a > b ? x : y;`. Also never fires today."""
    pattern = re.compile(r"return (?P<b>\w+) < (?P<a>\w+) \? (?P<x>\w+) : (?P<y>\w+);")

    def swap(match):
        return (f"return {match.group('a')} > {match.group('b')} ? "
                f"{match.group('x')} : {match.group('y')};")
    out, count = pattern.subn(swap, source)
    return out if count else None


# Not implemented, and named so the absence is deliberate rather than forgotten.
NOT_INVERTIBLE: dict[str, str] = {
    "drop-switch-default": (
        "the removed arm's return value is absent from the candidate, so no source-only rewrite can "
        "recover it and enumerating constants is unbounded. It is the one mutation that would need "
        "the TARGET ASSEMBLY -- the missing arm's constant is visible in the target's compare chain "
        "-- which this catalogue deliberately does not consult yet. Measured: 0 of 4 train tasks "
        "solved, and the held-out split contains no instance of it at all."),
}

# ENUMERATORS are inverses that cannot be written as one rewrite: the mutation destroyed the
# information, so the only honest move is to enumerate the plausible reconstructions and let the
# CERTIFICATE choose. They cost a handful of compiles each and no model calls.
ENUMERATORS: dict[str, object] = {}


def enumerator(name):
    def register(function):
        ENUMERATORS[name] = function
        return function
    return register


_TMP_DECL = re.compile(r"\bs32 tmp_(?P<tmp>\w+);")
_LOST_PAIR = re.compile(
    r"(?P<lead>\n[ ]*)(?P<assign>tmp_(?P<tmp>\w+) = (?P<expr>[^;]+);)\n"
    r"[ ]*\w+ = tmp_(?P=tmp);")
_SCALAR = re.compile(r"\bs32 (?P<name>\w+);")
_PARAMS = re.compile(r"\b\w+ (?P<fn>\w+)\((?P<params>[^)]*)\)\s*\{")


@enumerator("restore-lost-assignment")
def enumerate_lost_assignment(candidate: str) -> list[str]:
    """Reconstruct the assignment that `split_initialiser` silently re-bound.

    WHAT THE MUTATION DOES. `_MUL_DECL` reads group(1), the DECLARED variable, and group(2), the
    assignment TARGET, then emits a temporary built from group(1) and assigned to it:
    `var0 = arg0 * 3;` becomes `tmp_var1 = var0 * 3; var1 = tmp_var1;`. Both the target AND the
    operand move, which is why a single rewrite cannot undo it -- but the OPERATOR and the CONSTANT
    survive intact, so the set of plausible reconstructions is small and enumerable.

    WHAT IT SEARCHES. Every `<target> = <operand> <op> <constant>;` over the names the function
    actually declares or receives. On the frozen panel that is ~16 candidates per damaged task, each
    verified by the certificate, so an unsound guess is simply not accepted. It reads no answer and
    no target assembly -- only the candidate, which is text the solver was already handed.
    """
    # CLEAN FIRST, THEN MATCH. Splicing at an offset computed on the original text into the cleaned
    # text produced mangled output (`tmp_var1 = v` / `var0 = var0 * 3;+ 9;`) because the declaration
    # cleanup shifts every later offset.
    base = _TMP_DECL.sub("", candidate)
    base = re.sub(r"(?m)^([ ]*s32 \w+;)[ ]+$", r"\1", base)

    match = _LOST_PAIR.search(base)
    if not match:
        return []
    expr = match.group("expr").strip()
    parts = re.match(r"(?P<a>\w+)\s*(?P<op>[-+*/%&|^])\s*(?P<b>.+)", expr)
    if not parts:
        return []
    op, tail = parts.group("op"), parts.group("b").strip()

    names = [m.group("name") for m in _SCALAR.finditer(base)]
    fn = _PARAMS.search(base)
    if fn:
        for raw in fn.group("params").split(","):
            bits = raw.strip().split()
            if bits and bits[-1] != "void":
                names.append(bits[-1].lstrip("*"))
    names = [n for n in dict.fromkeys(names) if not n.startswith("tmp_") and n in base]
    if not names:
        return []

    out = []
    for target in names:
        for operand in names:
            rebuilt = f"{match.group('lead')}{target} = {operand} {op} {tail};"
            out.append(base[:match.start()] + rebuilt + base[match.end():])
    return out


def family_of(name: str) -> str:
    """`while-form+subtract-to-narrow` -> `while-form`; `restore-lost-assignment[7]` -> the family.

    Bookkeeping only: the receipt groups by the family that produced a candidate, so a combination or
    an enumerator index is attributed to what generated it rather than to a name that does not exist.
    """
    return name.split("+")[0].split("[")[0]


def repairs(candidate: str, *, combinations: bool = True) -> list[tuple[str, str]]:
    """Every deterministic inverse of the candidate, singles first then pairs.

    Pairs matter because a task carries ONE mutation, but some mutations perturb text another
    inverse also matches; applying two inverses can therefore be needed when the first rewrites a
    region the second also targets. They are tried and verified like everything else.
    """
    out: list[tuple[str, str]] = []
    singles = []
    for name, function in INVERSES.items():
        try:
            result = function(candidate)
        except Exception:                                  # noqa: BLE001 - a bad inverse is a no-op
            continue
        if result and result != candidate:
            singles.append((name, result))
            out.append((name, result))
    if combinations:
        for i, (first, once) in enumerate(singles):
            for second, function in INVERSES.items():
                if second == first:
                    continue
                try:
                    twice = function(once)
                except Exception:                          # noqa: BLE001
                    continue
                if twice and twice != once:
                    out.append((f"{first}+{second}", twice))
    # ENUMERATORS last: they are the expensive families, and a cheap single that certifies should be
    # tried first so the compile budget goes to the tasks nothing else reaches.
    for name, function in ENUMERATORS.items():
        try:
            produced = function(candidate)
        except Exception:                                  # noqa: BLE001
            continue
        for index, text in enumerate(produced or []):
            if text and text != candidate:
                out.append((f"{name}[{index}]", text))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", type=Path,
                    default=ROOT / "eval/results/local-posttraining-20260920/dataset/tasks.jsonl")
    ap.add_argument("--repo", type=Path, default=Path.home() / "decomp/sbk1")
    ap.add_argument("--split", default="test")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "eval/results/multichild-20260920/deterministic-repair.json")
    args = ap.parse_args(argv)

    from eval.repair_dataset_synth import DEFAULT_TARGET, compile_unit, resolve_recipe
    from eval.target_augment import compile_and_certify

    bundle = resolve_recipe(args.repo, DEFAULT_TARGET)
    resolved = bundle["provenance"]
    tasks = [json.loads(line) for line in
             args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    tasks = [t for t in tasks if t.get("split") == args.split]
    print(json.dumps({"split": args.split, "tasks": len(tasks),
                      "inverses": sorted(INVERSES), "enumerators": sorted(ENUMERATORS),
                      "not_invertible": sorted(NOT_INVERTIBLE)}),
          flush=True)

    work = ROOT / ".cache/deterministic-repair"
    rows = []
    families = list(INVERSES) + list(ENUMERATORS)
    per_inverse = {name: {"tried": 0, "exact": 0} for name in families}
    per_mutation: dict[str, dict] = {}
    solved = 0
    for task in tasks:
        candidate = (task.get("input") or {}).get("candidate") or ""
        answer = task.get("generator_source") or ""
        target_dir = work / "target" / task["task_id"].replace(":", "_")
        target_dir.mkdir(parents=True, exist_ok=True)
        built = compile_unit(args.repo, resolved, task["function"], answer, target_dir)
        if not built["compiled"]:
            rows.append({"task_id": task["task_id"], "error": "target did not compile"})
            continue
        target_object = target_dir / f"{task['function']}.o"

        exact_via, reproduced, tried = None, False, 0
        for name, text in repairs(candidate):
            tried += 1
            per_inverse[family_of(name)]["tried"] += 1
            case = work / "case" / task["task_id"].replace(":", "_") / name.replace("+", "_")
            outcome = compile_and_certify(args.repo, resolved, task["function"], text,
                                          case, target_object)
            if outcome["exact"]:
                per_inverse[family_of(name)]["exact"] += 1
                if exact_via is None:
                    exact_via = name
                    # DIAGNOSTIC ONLY: the repair never reads the answer. This just records whether
                    # the deterministic recovery landed on the recorded source as well.
                    reproduced = (text.strip() == answer.strip())
                break
        mutation = task.get("mutation") or "?"
        bucket = per_mutation.setdefault(mutation, {"tasks": 0, "solved": 0, "reproduced": 0})
        bucket["tasks"] += 1
        bucket["solved"] += bool(exact_via)
        bucket["reproduced"] += bool(reproduced)
        solved += bool(exact_via)
        rows.append({"task_id": task["task_id"], "mutation": mutation, "tried": tried,
                     "exact_via": exact_via, "reproduced_answer": reproduced})

    payload = {
        "split": args.split,
        "tasks": len(tasks),
        "solved_by_deterministic_repair": solved,
        "model_calls_used": 0,
        "per_mutation": per_mutation,
        "per_inverse": per_inverse,
        "not_invertible": NOT_INVERTIBLE,
        "detail": rows,
        "note": ("A repair that certifies is a certified match, but the mutation catalogue is the "
                 "curriculum's own definition of the defect, so this measures how much of the panel "
                 "is a fixed-function inversion rather than a repair search."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nsolved by deterministic inverse repair: {solved}/{len(tasks)}   (model calls: 0)")
    print(f"{'mutation':22s} {'tasks':>6} {'solved':>7} {'also matched the answer':>24}")
    for name, counts in sorted(per_mutation.items()):
        print(f"{name:22s} {counts['tasks']:6d} {counts['solved']:7d} "
              f"{counts['reproduced']:24d}")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
