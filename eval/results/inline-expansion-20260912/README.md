# Bounded explicit helper expansion

`solver.inline_expansion.candidates(source, function, max_variants=4)` now emits
one source-bound call-site expansion per variant. Helpers must already have a
unique definition in the supplied candidate. The caller is the only function
edited; the helper definition and all unrelated text are retained.

The supported helper body is exactly one integer `return` expression. Its only
value identifiers are parameters, each used exactly once. The small expression
grammar accepts integer arithmetic, bitwise operations, comparisons and integer
casts. It rejects calls/recursion, memory access, globals, assignment, increments,
short-circuit/conditional expressions, local statements and control flow. Actuals
are integer literals or nonvolatile builtin-integer caller parameters, with
conservative local-redeclaration checks. Explicit casts preserve each helper
parameter conversion and the helper's result conversion. Simultaneous substitution
and no generated variable names prevent expansion-name capture.

Literal `#include` directives remain available for normal candidate compilation;
other preprocessing and visible volatile/assembly constructs decline. Types and
macros inside includes remain unknown. The generator guesses no external scalar
aliases and loads no external or reference C. Included macro effects and arbitrary
C scopes are not proven by this text-level search operator. Compiler, differential
and exactness gates remain mandatory.

The normal `code_shapes.candidates` round robin now reaches the operator, and a
test exercises that path through
`differential_repair_pilot.deterministic_exactness_candidates`, including its hard
budget. The generator has a maximum 16 variants even if a larger cap is requested;
the default remains four. Source size, expression size/token count and parameter
count are bounded as well.

## Actual target compiler measurement

The isolated fixture compares a synthetic explicit target with a helper-call
candidate and the **actual generator output**. It is not a held-out game function,
campaign win, model result or candidate import. No reference C was used.

The IDO target compiler at
`/home/grant/decomp/sbk1/tools/ido-recomp/linux/cc` compiled all three sources with
`-O2 -mips1 -G 0 -non_shared` and the project's ordinary additional compiler flags.
All command lines, source/compiler/generator hashes, exit statuses and output are
retained in `receipt-run-1789243694999098555.json`. Full sources, objects and
disassembly live in the fresh native workspace
`/home/grant/decomp/inline-expansion-20260912/run-1789243694999098555`.

| Form | Function f instruction words | Result |
| --- | --- | --- |
| Explicit target | `00047080 01c51026 03e00008 24420007` | 4 instructions |
| Helper-call candidate | `27bdffe8 afbf0014 0c000000 00000000 8fbf0014 27bd0018 03e00008 24420007` | 8 instructions, including call/frame |
| Generator expansion | `00047080 01c51026 03e00008 24420007` | exact function-byte equality with fixture target |

This demonstrates that the new actuator can remove a real target-compiler call
residual. It does not establish broad transfer to large campaign functions.
The whole translation unit still contains the retained helper; the comparison
above is the explicitly selected `f` function, not whole-object equality.

Repeat the isolated experiment without overwriting existing work:

```powershell
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/c/Code/gameDecomp && python3 -m eval.results.inline-expansion-20260912.pilot'
```

Each invocation creates a new `run-<time_ns>` directory. The script logs every
compiler outcome before assertions and performs no workspace/KB scoring or import.

## Verification

`tests/test_inline_expansion.py` contains motivating firing, budget, scope,
conversion, lexical, dangerous-expression, ambiguous-definition and normal-search
wiring tests. Native C89 checks at both `-O0` and `-O2` compare the original and
expanded narrow-parameter/narrow-return functions for 1,203 integer inputs each.
All **47 tests passed** in the approved native-compiler environment. The default
Windows sandbox could not run its discovered compiler; that failed test receipt
was not treated as a pass or worked around by weakening tests. Root also ran the
earlier 46-test version in WSL.
