# Prologue-only residual combinations

Eight final source compositions were tested on the backend agent's97.979 candidate. Its entire instruction sequence after the first13 normalized lines matches the target, including scratch-register choices. Only the prologue remains different.

No composition reached exactness or improved the98.511 score champion:

- A subsequent unsigned normalization, extra selector cast, s32/s16 or u32/s16 chains, and a separate signed16-bit local all retained97.979 with the same prologue-only residual.
- Moving signed narrowing exclusively into the8-bit selector restored the98.156 candidate with the uniform scratch-register shift. This indicates that assigning the signed-narrowed value back to the parameter is material to the corrected scratch cycle.
- Moving the preserved value to an unsigned local regressed to94.943.

The double-normalization candidate `00.c` independently passes **76/76 semantic cases**, zero failed or inconclusive cases; see `replay.json`. It preserves the clean prologue-only residual for subsequent diagnosis. The retained similarity-score champion remains98.511. These diagnostic variants were not integrated into production search.

No compiler flags, assembly, build guards, or production source were changed. Source probing stops at this checkpoint.
