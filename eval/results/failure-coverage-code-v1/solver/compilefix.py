"""Route a compile failure to the fixes that address it.

`solver/diagnose.py` already does this for MISMATCH verdicts -- classify, then
name the playbook. Nothing did it for compile failures, so every repair pass
was applied blind in a fixed order and each new population re-discovered by
trial which ones mattered.

Measured over all 4,027 non-compiling attempts ever logged: 118 distinct error
signatures, of which **ten cover 90.1%**.

     50.0%  Syntax Error                                (see SUB-SIGNATURES)
     12.7%  X undefined                                 solver/globaldecl.py
      7.8%  The C file contains a do-while loop         rewrite_do_while
      6.4%  Compiled object has no text symbols         -- no fix
      3.6%  Duplicate member X                          OUR BUG, fixed
      2.4%  Selector requires struct/union pointer      solver/memberaccess.py
      1.6%  Constants must have arithmetic type         OUR BUG
      1.3%  Unknown character ` ignored                 OUR BUG (fence leak)
      1.2%  Cannot open file stddef.h                   strip non-common.h

Roughly 11% of every compile failure in the database was produced by our own
generators emitting invalid C -- duplicate padding names, a markdown fence left
in the source, an argument swap that moved a cast to an invalid position. Those
are not model failures and not difficulty. A registry makes them visible
instead of letting them accumulate as 440 wasted attempts.

WHY SUB-SIGNATURES. `Syntax Error` is half of everything and is not one cause.
IDO reports it for any malformed declarator, so the useful discriminator is in
the SOURCE, not the message: an undeclared parameter type, a primitive pointer
the body dereferences, or an undeclared type somewhere other than a parameter
all arrive as the same three words.

An unrecognised signature returns no fixes AND is reported by `gaps()`. A pass
that silently declines is indistinguishable from a pass with nothing to do --
this project's fifth rule -- so an unroutable error is a named finding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from solver import typedecl, unknowns

# Strip the per-attempt noise: temp path, line number, quoted identifiers,
# hex constants. What remains is stable across attempts and functions.
_PATH = re.compile(r"^.*?\.build-source\.\w+\.c[:,]?\s*")
_LINE = re.compile(r"^\s*(?:line\s+)?\d+:\s*")
# Line numbers appear INSIDE the message too: `redeclaration of 'x'; previous
# declaration at line 13 in file 'y'`. Stripping only the leading one split
# that single cause into four separate signatures at 33/28/25/20 occurrences,
# each below the threshold where anyone would look at it.
_INNER_LINE = re.compile(r"\bat line \d+\b")
_IN_FILE = re.compile(r"\bin file X\b")
_QUOTED = re.compile(r"'[^']*'")
_HEX = re.compile(r"\b0[xX][0-9A-Fa-f]+\b")


def signature(stderr: str) -> str:
    """A stable label for one compiler failure, or '' when there is none."""
    text = (stderr or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0]
    first = re.sub(r"^cfe:\s*Error:\s*", "", first)
    first = _PATH.sub("", first)
    first = _LINE.sub("", first)
    first = _QUOTED.sub("X", first)
    first = _HEX.sub("N", first)
    first = _INNER_LINE.sub("at line N", first)
    first = _IN_FILE.sub("", first)
    return " ".join(first.split())[:64]


# --- sub-signatures for the 50% bucket --------------------------------------

UNDECLARED_PARAM_TYPE = "Syntax Error/undeclared-param-type"
CONTRADICTED_POINTER = "Syntax Error/contradicted-primitive-pointer"
UNDECLARED_ELSEWHERE = "Syntax Error/undeclared-type-not-a-parameter"


def refine(sig: str, code: str, func: str, known_types: set[str]) -> str:
    """Split `Syntax Error` by what the SOURCE shows, not what IDO said."""
    if not sig.startswith("Syntax Error"):
        return sig
    params = typedecl.pointer_parameters(code, func)
    if not params:
        return UNDECLARED_ELSEWHERE

    # Two passes, contradiction first, because a draft routinely has BOTH: on
    # Fcutoff arg0 is an undeclared struct AND arg1 is a `u8 *` the body
    # dereferences. Returning on the first undeclared parameter routed it to
    # typedecl, which was measured NOT to unblock it -- byte-indexing did. Same
    # lesson as bucket ordering: prefer the more specific and more independent
    # cause, not the first one encountered.
    for _index, type_name, var in params:
        if (type_name in known_types or type_name in typedecl.PRIMITIVE_TYPES
                ) and unknowns.draft_capabilities(code, var)["members"]:
            return CONTRADICTED_POINTER
    for _index, type_name, _var in params:
        if (type_name not in known_types
                and type_name not in typedecl.PRIMITIVE_TYPES
                and not typedecl.declared_in(code, type_name)):
            return UNDECLARED_PARAM_TYPE
    return UNDECLARED_ELSEWHERE


# --- the registry -----------------------------------------------------------

@dataclass(frozen=True)
class Fix:
    name: str                  # the stage label the harness records
    module: str                # where it lives, for the receipt
    note: str = ""


DO_WHILE = Fix("do-while", "tools.score_repo_function",
               "the build bans the `do` TOKEN, not the loop shape")
TYPEDECL = Fix("typedecl", "solver.typedecl",
               "declare the struct from observed offsets")
BYTE_INDEX = Fix("byte-index", "solver.memberaccess",
                 "one-byte pointer: offset IS the index, do not invent a type")
GLOBALS = Fix("globals", "solver.globaldecl",
              "extern from evidence width; extent deliberately unspecified")
STRIP_INCLUDES = Fix("strip-includes", "solver.llm.strip_unresolvable_includes",
                     "the N64 build has no stddef.h/stdint.h; keep common.h")
STRIP_REDECL = Fix("strip-redeclarations", "solver.buildtypes",
                   "drop a duplicate of a type the build already declares")

REGISTRY: dict[str, tuple[Fix, ...]] = {
    UNDECLARED_PARAM_TYPE: (TYPEDECL, GLOBALS),
    CONTRADICTED_POINTER: (BYTE_INDEX, TYPEDECL),
    # TYPEDECL first: it reads pointer LOCALS as well as parameters, and this
    # signature is by definition the case where the undeclared type is not on a
    # parameter. Routing it to GLOBALS alone meant the locals support could not
    # fire at all -- 66 of 143 functions stopped at "every registered fix
    # declined" while the fix for them existed and was never dispatched to.
    UNDECLARED_ELSEWHERE: (TYPEDECL, GLOBALS),
    "X undefined; reoccurrences will not be reported.": (GLOBALS,),
    "ERROR: The C file contains a do-while loop.": (DO_WHILE,),
    "Selector requires struct/union pointer as left hand side": (
        BYTE_INDEX, TYPEDECL),
    "Subscripting a non-array.": (BYTE_INDEX, TYPEDECL),
    "Empty declaration specifiers": (TYPEDECL,),
    # Both fixes already existed and neither was reachable from this path.
    "Cannot open file stddef.h for #include": (STRIP_INCLUDES,),
    "Cannot open file stdint.h for #include": (STRIP_INCLUDES,),
    "Cannot open file string.h for #include": (STRIP_INCLUDES,),
    "redeclaration of X; previous declaration at line N": (STRIP_REDECL,),
}

# Prefixes that only resolve once refined against the source. `gaps()` must not
# report these as unrouted -- the first run did exactly that, listing
# `Syntax Error` at 50% as the top gap while every one of its sub-signatures
# had a registry entry.
REFINED_PREFIXES = ("Syntax Error",)

# Signatures we understand and deliberately have no fix for. Listed so they do
# not show up as gaps forever, and so the reason is written down once.
KNOWN_UNFIXABLE: dict[str, str] = {
    # Keys must be EXACTLY what signature() emits. These were first copied from
    # a census script that truncated at 60 characters while signature()
    # truncates at 64, so none of them matched and every one resurfaced as a
    # gap. A registry keyed by a string is only as good as the one function
    # that produces the string.
    "ERROR: Compiled object has no text symbols. Check for type confl":
        "m2c produced no body; needs an independent decompiler, not a repair",
    "Duplicate member X":
        "our own generator; fixed at source in solver/diffrepair.py",
    "Unknown character ` ignored":
        "markdown fence leaked past extraction; a harness bug, not a candidate",
    "Constants must have arithmetic type.":
        "argswap moved a cast to an invalid position; our own generator",
}


def dispatch(stderr: str, code: str, func: str,
             known_types: set[str]) -> tuple[str, tuple[Fix, ...]]:
    """(refined signature, fixes to try in order). Empty tuple means no fix."""
    sig = refine(signature(stderr), code, func, known_types)
    return sig, REGISTRY.get(sig, ())


def gaps(signatures: dict[str, int]) -> list[tuple[str, int]]:
    """Signatures with no registered fix and no recorded reason, worst first.

    This is the output that matters: an error nothing routes is a named gap in
    the solver, not a hard function.
    """
    return sorted(((sig, n) for sig, n in signatures.items()
                   if sig and sig not in REGISTRY
                   and sig not in KNOWN_UNFIXABLE
                   and not sig.startswith(REFINED_PREFIXES)),
                  key=lambda pair: -pair[1])
