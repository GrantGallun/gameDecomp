# Width edits — results

Pre-registration: `PREREGISTRATION.md`. Raw data: `results.jsonl` (299 rows, 0 harness errors, pilot rows
kept since the harness didn't change). Attempts: `/home/grant/decomp/runs/width-edits-20260929/trial.sqlite`.
Scripts: `analyse.py`, `rejected.py`.

| | all | small | medium | large+ |
|---|---:|---:|---:|---:|
| functions | 299 | 33 | 109 | 157 |
| fired (a `decl`/`type` edit compiled) | 214 | 32 | 97 | 85 |
| acted on its class (extension units fell) | 106 | 17 | 49 | 40 |
| extension units cleared to 0 | 19 | 5 | 10 | 4 |
| best score improved | 49 | 7 | 21 | 21 |
| exact | **0** | 0 | 0 | 0 |

## Against the predictions

- **Fire ≥ 80%: missed, 72%.** Not the < 50% "broken mechanism" case. Every one of the 85
  non-firing functions had width edits *proposed*; none was among the first 24 compiled (`ORDER`
  puts `decl`/`type` after literal, copy-direction and read-back edits, and `per_step` is 24).
  Large+ fires in only 54%.
- **Acted 30–60%: held, 35%.**
- **Exact 3–15: failed, 0.** Retyping alone finished no function in this class.

## Why nothing finished

1. **The class never occurs alone.** The 19 functions whose extensions were fully cleared still had
   instruction distance 4–116 in their best state (e.g. `updateControllerPakRaceRecordSavePrompt…`
   97.96 → 99.87, gradient `[0,25,4] → [0,4,0]`). Clearing one class exposes the next.
2. **Half the correct edits are rejected by the acceptance rule.** Of the 106 functions where a child
   reduced extension units, instruction distance also fell in 41, stayed equal in 9, and **rose in 56**.
   The search keeps only children that improve the whole gradient, so a class-correct retype whose
   knock-on shifts other instructions is discarded before a follow-up edit can use it.

## Reading

The width mechanism works on its own class. What stops it finishing is composition: chaining with
the owners of the other classes on the same function, plus an acceptance rule that doesn't discard
progress on one class for knock-on elsewhere. That's a search-policy change, and earlier
policy-only changes produced nothing (`memory/hypothesis-graveyard.md`). This one is different
because it's motivated by a measured rejection, but it needs its own pre-registered test before
anything is installed.
