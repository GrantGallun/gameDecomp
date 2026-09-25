# Result: m2c needs layouts AND prototypes, and a binary-only context supplies most of both

Protocol: `PROTOCOL.md` (amendments A1-A6, each dated before the run it governs). 600 mining functions (the scored
population and sealed sets excluded; the reference is used here as an experimental treatment, never as input to a
scored function). The generator was developed on DEV (the first 150) and frozen (`freeze.json`) before CHECK (the
other 450). BINARY compiles standalone behind a clean prelude (public libultra/SDK headers only); the other arms
compile inside the reference TU.

## Four-arm ablation (all 600): "both", and they compound
| context | exact | compiles |
|---|---|---|
| NONE (assembly only) | 22 (3.7%) | 23 |
| NOPROTO (reference layouts, no prototypes) | 33 (5.5%) | 47 |
| NOLAYOUT (reference prototypes, no layouts) | 70 (11.7%) | 79 |
| FULL (the reference team's context) | 205 (34.2%) | 426 (71%) |
Removing prototypes loses 172 of FULL's 183 extra exacts; removing layouts loses 135. Neither alone recovers much.
The type work must recover both together.

## BINARY on CHECK (450 held-out functions, generator frozen)
| size | n | NONE | NOLAYOUT | **BINARY** | FULL |
|---|---|---|---|---|---|
| small (< 50) | 268 | 17 / 17 | 55 / 60 | **111 / 231** | 148 / 247 |
| medium (< 150) | 124 | 2 / 2 | 2 / 3 | **5 / 57** | 11 / 67 |
| large | 58 | 0 / 0 | 0 / 0 | **0 / 6** | 0 / 7 |
| all | 450 | 19 / 19 | 57 / 63 | **116 / 294** | 159 / 321 |
(exact / compiled-or-exact)

- BINARY: **25.8% exact, 65% compile**, from binary evidence only: 6x NONE's exact count, and **69% of the
  FULL-minus-NONE exact gap** (97 of 140), at 92% of FULL's compile count.
- DEV under the clean prelude was 34/150 exact; CHECK 116/450 (25.8%) versus DEV 22.7%, so no sign of overfitting to
  DEV.
- The medium/large gap to FULL is mostly compile-level already closed (57 vs 67 medium compile) but not exact: the
  structural wall (branch shape, allocation), which is not a type problem.

## Integrity notes
- The reference `common.h` includes `game/math/geometry.h`; BINARY excludes it (A6). DEV was identical with and
  without it (34 exact), measured rather than assumed.
- The identity variant (D32) was chosen on FIT labels: one threshold selected with reference labels. Disclosed; no
  per-function reference input.
- The masked comparison treats relocation symbol names as equal; recording goes through the official scorer
  (`binary-types-capability-20260924`).
