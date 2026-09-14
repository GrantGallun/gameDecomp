# Callback control experiments

Starting from the verified 94.723 callback, 12 guarded traversal/control alternatives compiled. The guarded infinite `for` loop with an explicit bottom null check reached **97.454**, independently reproducing the reload agent's discovery. This avoids the project's prohibited `do-while` form.

On the reload agent's 97.454 source, 12 allocation/index alternatives and all 23 other orders of the four final node-field stores compiled. None improved the score; the existing field order remained strongest. Two repeated baselines bring this branch to 49 builds total.

The outstanding residual contains a missing explicit sentinel-address addition and register differences. The experiments reject the hypothesis that simply moving final field stores or collapsing the pool index is sufficient to finish the match.

`solver/callback_control_alternatives.py` provides the bounded traversal hypotheses. Four focused tests pass, covering function boundaries, the bottom null check, live cursor rejection, float comparison rejection, stale sentinel aliases, and deterministic budgets. Acceptance still requires semantic replay and the object certificate. The equivalent independently discovered 97.454 shape's full replay is handled by the reload branch; this report makes no independent new semantic-pass claim.

Sources and byte diffs are retained in v1 and v2. No production source, compiler flags, or build guards were modified.
