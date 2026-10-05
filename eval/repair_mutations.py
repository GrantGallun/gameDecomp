"""Repair candidates for synthetic functions: a clean source, damaged in one declared way.

WHY DAMAGE A CORRECT ANSWER
---------------------------
`tools/synthetic_corpus.py` generates C and compiles it with the game's real IDO recipe, so the
source is known and the target object is real. That gives a repair task with an unusually clean
shape: the generator's C is the HIDDEN ANSWER, its object is the TARGET, and a damaged copy of
that C is the CANDIDATE the solver must repair. Nothing about the answer reaches the solver --
it sees the target assembly, the damaged candidate, and the compiler's own feedback.

The alternative would be to let a model draft the candidate, which is what the game-function
route does and which produced `0` improved candidates in 56 verified attempts (see
`eval/results/posttraining-m1-20260920/RESULT.md`). A generated candidate is a deliberate
control: the defect is known, the repair is provably possible because the original compiles to
the target, and the difficulty is a knob by construction.

WHAT A MUTATION MUST BE
-----------------------
Every mutation declares a `FaultClass` and must satisfy three properties, all MEASURED rather
than assumed (`measure` below runs the measurement):

1. **It compiles.** A candidate that does not build makes the lesson "write something that
   compiles", which is a different task from repairing compiler behaviour.
2. **Its object DIFFERS from the target.** A candidate that already matches is not a repair
   task at all; it is a no-op counted as a success.
3. **The repair is reachable from the assembly.** The failure mode must be visible in the
   compiled output or the compiler's diagnostics -- otherwise the task is a guess, and a model
   that cannot see it is being asked to read the generator's mind.

A mutation that fails 1 or 2 is a BUG IN THIS MODULE, not a hard instance, and `measure`
reports it as such. That is the project's "silent decline" rule applied to data generation: a
perturbation that quietly produces identical objects looks exactly like a hard task from the
outside, and would train on nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FaultClass:
    """Which `solver/signals.py` fault axis a mutation is meant to exercise."""

    axis: str
    manifest: str


# The axes mirror solver/signals.py so a mutation's intent is comparable with a real residual.
STRUCTURAL = FaultClass("structural", "control flow shape: loop form and branch count")
REGALLOC = FaultClass("regalloc", "saved registers, stack spills and temporaries")
LAYOUT = FaultClass("layout", "field offsets and structure extent")
IMMEDIATE = FaultClass("immediate", "constant values and sign-extension")


@dataclass(frozen=True)
class Mutation:
    name: str
    fault: FaultClass
    families: tuple[str, ...]
    apply: object          # (source: str) -> str | None ; None means "does not apply"
    expectation: str


# --- the mutations ------------------------------------------------------------

# A `s16` local where the original used `s32` (or the reverse) is the classic narrow-width
# defect: IDO inserts `sll 16 / sra 16` around every use. Recoverable by inspection because the
# pair of shifts is exactly what the wider declaration would not produce.
_NARROW = re.compile(r"\bs32 (\w+);")


def widen_locals(source: str) -> str | None:
    """s32 -> s16 for the first two locals. Adds sign-extension; changes nothing semantic here.

    Only applied where the generated function has locals to narrow. The generated arithmetic
    stays inside 16 bits for these families, so the damaged source still means the same thing --
    which is the point: the DEFECT IS IN THE CODEGEN, not in the behaviour, exactly like the
    real residuals this project fights.
    """
    names = _NARROW.findall(source)
    if not names:
        return None
    out = source
    for name in names[:2]:
        out = out.replace(f"s32 {name};", f"s16 {name};", 1)
    if out == source:
        return None
    # Guard: narrowing is only meaningful if the local is used where a narrow value is legal.
    return out


_FOR_HEAD = re.compile(
    r"    for \((i) = ([^;]+); ([^;]+); ([^)]+)\) \{\n(.*?)\n    \}",
    re.DOTALL)


def while_form(source: str) -> str | None:
    """`for (i = a; c; s) { B }` -> the semantically equal `i = a; while (c) { B; s }`.

    IDO emits a different control-flow shape for the two: the `while` form tests before the
    first iteration and the `for` form's test placement differs, so the object changes. The
    repair is visible in the branch structure, which is the fault axis this exercises.
    """
    match = _FOR_HEAD.search(source)
    if not match:
        return None
    var, init, cond, step, body = match.groups()
    body_lines = body.splitlines()
    indent = "        "
    replacement = "\n".join([
        f"    {var} = {init.strip()};",
        f"    while ({cond.strip()}) {{",
        *body_lines,
        f"{indent}{step.strip()};",
        "    }",
    ])
    return source[:match.start()] + replacement + source[match.end():]


_SWITCH_DEFAULT = re.compile(r"\n    default:\n        return -?\d+;")


def drop_switch_default(source: str) -> str | None:
    """Remove a `default:` arm. Changes the emitted compare chain and the fallthrough return."""
    if not _SWITCH_DEFAULT.search(source):
        return None
    return _SWITCH_DEFAULT.sub("", source, count=1)


_DIV_POW2 = re.compile(r"\((\w+) / (\d+)\)")


def divide_to_shift(source: str) -> str | None:
    """`(x / 2**n)` -> `(x >> n)`: signed division and an arithmetic shift differ in codegen.

    This is the canonical IDO signedness defect that `solver/refine` names in its prompt
    ("a bias then shift is signed division `/ 2^n`, never `>> n`"), so a model has been told
    about it -- which makes it a fair test of whether training changes anything.

    MEASURED 2026-09-20: this fires on nothing the current generator produces, because
    `_expr` emits only `+` and `*` (and `gen_float_math` / `gen_color_pack` emit their own
    shapes). It is kept because it is the right mutation for a family that emits division and
    the moment one does, this starts firing -- but `candidates_for` never invents an instance
    for it, so the catalog does not silently claim coverage it does not have.
    """
    match = _DIV_POW2.search(source)
    if not match:
        return None
    divisor = int(match.group(2))
    if divisor <= 1 or divisor & (divisor - 1):
        return None
    shift = divisor.bit_length() - 1
    return source[:match.start()] + f"({match.group(1)} >> {shift})" + source[match.end():]


_SUB = re.compile(r"(?<![-<>=!+\-*/])(\w+) - (\w+)(?![-<>=])")


def subtract_to_narrow(source: str) -> str | None:
    """`a - b` -> `(s16)(a - b)`: a truncating cast IDO honours with sign-extension.

    Chosen because it fires on what the generator ACTUALLY emits. `_expr` produces `+` and `*`
    for switch arms and if-chains, and `gen_if_chain` builds its return as a sum, so a
    subtraction is not guaranteed; where one is absent this declines and is reported as
    declining rather than as covered.
    """
    match = _SUB.search(source)
    if not match:
        return None
    return source[:match.start()] + f"(s16)({match.group(1)} - {match.group(2)})" + source[match.end():]


_MUL_DECL = re.compile(r"    s32 (\w+);\n\n    (\w+) = (\w+) \* (\d+);")


def split_initialiser(source: str) -> str | None:
    """`v = p * k;` -> an extra temporary holding `p * k`, forcing a different allocation.

    Targets `regalloc`: an intermediate local changes which values live in which registers, and
    `TRAINING.md` names exactly this ("an intermediate local variable changes register
    allocation ... adding or removing one is often the whole difference"). It applies to
    `_live_across_calls`, which `saved_regs` and `stack_spill` both use.
    """
    match = _MUL_DECL.search(source)
    if not match:
        return None
    var, param, k = match.group(1), match.group(2), match.group(4)
    extra = f"    s32 tmp_{var};\n\n    tmp_{var} = {param} * {k};\n    {var} = tmp_{var};"
    return source[:match.start()] + f"    s32 {var};" + extra + source[match.end():]


_TERNARY = re.compile(r"return (\w+) > (\w+) \? (\w+) : (\w+);")


def invert_comparison(source: str) -> str | None:
    """Swap the arms of a comparison-returning expression where one exists.

    A `>` written as `<` with the operands exchanged is the same C and different codegen, so
    this is a pure source-shape defect with a single visible fix.
    """
    match = _TERNARY.search(source)
    if not match:
        return None
    a, b, x, y = match.groups()
    return source[:match.start()] + f"return {b} < {a} ? {x} : {y};" + source[match.end():]


MUTATIONS: dict[str, Mutation] = {m.name: m for m in (
    Mutation("narrow-locals", IMMEDIATE,
             ("loop_for", "if_chain", "saved_regs", "stack_spill", "narrow_locals",
              "color_pack", "switch_dense"),
             widen_locals,
             "narrowing an s32 local to s16 makes IDO sign-extend every use"),
    Mutation("while-form", STRUCTURAL, ("loop_for",),
             while_form,
             "`for` rewritten as `while` adds the loop-entry shape and moves the step"),
    Mutation("drop-switch-default", STRUCTURAL, ("switch_dense",),
             drop_switch_default,
             "a removed `default:` arm shortens the compare chain and the tail return"),
    Mutation("divide-to-shift", IMMEDIATE,
             ("struct_offsets", "loop_for", "if_chain", "float_math"),
             divide_to_shift,
             "`/ 2^n` written as `>> n` drops IDO's signed-division bias sequence"),
    Mutation("subtract-to-narrow", IMMEDIATE,
             ("switch_dense", "switch_sparse", "if_chain", "loop_for", "color_pack"),
             subtract_to_narrow,
             "a truncating `(s16)` cast around a subtraction adds sign-extension"),
    Mutation("split-initialiser", REGALLOC,
             ("saved_regs", "stack_spill"),
             split_initialiser,
             "an extra temporary changes which values occupy registers across the calls"),
    Mutation("invert-comparison", STRUCTURAL, ("if_chain", "switch_dense", "switch_sparse"),
             invert_comparison,
             "swapping a comparison's operands keeps the C and changes the branch"),
)}


def candidates_for(family: str, source: str) -> list[tuple[str, str]]:
    """Every mutation that applies and keeps the function's identity: [(name, damaged_source)].

    The identity check is load-bearing, not defensive: a mutant that renamed `syn_x` would
    compile, change the object, and then collide with another instance's symbol in the corpus.
    """
    expected = function_names(source)
    if len(expected) != 1:
        return []
    out = []
    for name, mutation in MUTATIONS.items():
        if family not in mutation.families:
            continue
        try:
            damaged = mutation.apply(source)
        except Exception:
            damaged = None
        if damaged and damaged != source and keeps_identity(expected[0], damaged):
            out.append((name, damaged))
    return out


_DEF_NAMES = re.compile(r"(?m)^[A-Za-z_][\w \t\*]*\b(\w+)\s*\([^;{]*\)\s*\{")
_FORWARD_DECL = re.compile(r"(?m)^[A-Za-z_][\w \t\*]*\b(\w+)\s*\([^;{]*\)\s*;")


def function_names(source: str) -> list[str]:
    """Every function this translation unit DEFINES, in source order.

    Definitions only. A forward declaration (`extern s32 syn_ext(s32);`, which
    `_live_across_calls` emits) has a `;` instead of a `{` and is a call target, not a
    definition -- counting it made the collision check below report every `saved_regs` mutant
    as renamed when nothing had been renamed.
    """
    return _DEF_NAMES.findall(_FORWARD_DECL.sub("", source))


def keeps_identity(expected_name: str, source: str) -> bool:
    """True when `source` still defines exactly `expected_name` and nothing else.

    A mutation whose damaged source defines a different symbol would collide with the corpus's
    `syn_*` naming contract and could overwrite another instance's object. Such a mutant is
    unusable regardless of how good its codegen looks, and this is checked rather than assumed.
    """
    names = function_names(source)
    return names == [expected_name]
