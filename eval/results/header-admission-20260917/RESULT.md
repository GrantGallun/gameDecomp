# The admission wall has a route, and it is bigger than I said

## Correction first: the wall is 1,633 functions, not 166

`166` was the do-bearing subset. The population of functions with an m2c draft and **no compiling
attempt at all** is **1,633**. That is the real size of the admission wall, and it is the largest single
pool in the project.

## The route, and how it was established

Not by reasoning — by reading the tree. The drafts die on declarations like

```c
    PlayerCommandState *var_s0;
```

with `Syntax Error` / `Empty declaration specifiers`, and `typedecl` declines because it can place only
one member name (`pdata`) among 60 pooled offsets, which would be "a guess dressed as a layout". That
refusal is right. **But the type is not unattributable — it is already written down:**

    include/game/audio/audio_engine.h:61   typedef struct PlayerCommandState {
    include/game/audio/audio_engine.h:154  } PlayerCommandState;

So the error is a **missing include**, and the header resolves it.

## The composition that was missing

The header stage alone does not admit: it moves the failure. Measured on `MusStartEffect`, after
`game/audio/audio_engine.h` resolves `PlayerCommandState` the terminal errors become

    'mus_channels' undefined; reoccurrences will not be reported.
    'max_channels' undefined; reoccurrences will not be reported.
    'gSoundPriorityTable' undefined; reoccurrences will not be reported.

— the class `solver.compilefix` **already registers** as
`"X undefined; reoccurrences will not be reported." -> GLOBALS` (`solver.globaldecl`), and which
`zero_token_harvest.repair_chain` already applies. Running that chain on the ORIGINAL draft could not
help, because the draft did not parse yet. **Header first, then repair-chain** is the composition neither
stage had on its own.

| sample of the 7 known failures | before | after |
|---|---:|---:|
| compiling | **0** | **2** |

`MusStartEffect` and `MusStartEffect2` now compile; `MusStartEffect2` at score 72.603.

## What this is, and is not

A candidate that builds only after being handed a reconstructed `include/game/**` header is
**HEADER-ASSISTED** by this project's own taxonomy — neither a copied body nor something reachable from
binary evidence. So this route produces **ADMISSION, never matches**, and is reported as such. Its value
is different and specific: 1,633 functions have *no attempt at all*, and an attempt is what the knowledge
base needs before it can learn anything about them.

## Two bugs in my own driver, both found by running it

1. **The early return skipped the stage that owns the error.** When no declaring header was found the loop
   `return`ed, so the repair-chain stage never ran — which is how the first version reported
   `no-declaring-header` on all seven and compiled none. Changed to `break`.
2. **A missing signature parameter.** `run_one` had to accept the shared `context` (pool + symbol table)
   or the globals stage could not declare externs from evidence.

## In flight

`eval/header_admission.py` over all 1,633, logging every attempt with strategy
`header-admission:roundN` / `header-admission:repair-chain`. Its summary prints compiled, exact and the
status counts. At the sample rate this is worth tens of newly-compiling functions, and every one of them
is a function the pipeline could not previously score at all.
