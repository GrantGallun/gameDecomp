# Selector allocation hypothesis

Twenty-four new source relationships plus one98.511 control were compiled through production IDO. All compiled; none exceeded **98.511**.

- Preserving the full type in u16/u32 locals before/after dispatch, using one full-width carrier for dispatch and final field storage, and putting assignments inside the switch expression were compiler-inert.
- Signed carriers sometimes changed allocation but regressed to95.794.
- Splitting the16-bit preserved type into high and low components added work and regressed to93.050.
- Positive counter guards changed control flow substantially; explicit casted decrement stores and subtraction assignment were inert.

This weakens the hypothesis that the prologue's one-register shift is caused directly by the spelling of the selector narrowing. The same selector operations survive different source relationships. Downstream allocation/liveness remains a more plausible lever; backend inspection is needed to distinguish it.

No candidate was retained and no new semantic-pass claim is made. These hypotheses remain outside the production search. `solver/callback_selector_alternatives.py` contains the reproducible bounded experimental generator; its three focused tests pass. Sources and instruction diffs are saved beside `summary.json`.

No production source, compiler flags, build guards, or assembly were modified.
