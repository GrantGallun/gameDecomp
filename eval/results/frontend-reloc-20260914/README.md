# Frontend fix-its and relocation-name repairs (2026-09-14)

Target sets were exported read-only from checkpoint 16791 by `export_targets.py`:
- 29 pending functions that compile but are rejected by the project clang frontend;
- 214 functions with a relocation-name mismatch, 28 of them relocation-only.

## Frontend fix-its (`solver/frontend_fixits.py`, `tests/test_frontend_fixits.py`)

The fix re-runs the project's own frontend command with `-fdiagnostics-print-source-range-info` and wraps each
diagnosed expression in the cast clang names. Covered: incompatible pointer types, int conversions, and pointer
arithmetic on an incomplete type (`u8 *`). Rounds repeat until the frontend passes.

Real clang range ends are **exclusive**. The first version assumed start-of-last-token and applied nothing, so the
test now uses real output.

`probe_frontend.py` → `frontend-probe/summary.json`:
- **28 of 29 now pass the frontend.** 1 is object-exact (fadeOutAllMusicSequences), 26 kept or raised their score
  (3 improved, by up to +3.2), and 1 dropped slightly (80.13 → 79.53, from an incomplete-type `u8 *` cast).
- 1 still fails: an implicit function declaration, which casts cannot fix.
- Frontend-rejected nodes sit in the campaign's frontend lane, where only compile recovery and model profiles run.
  Passing moves them to the byte and semantic lanes (register search, deterministic search).

## Relocation names (`solver/relocation_names.py`, `tests/test_relocation_names.py`)

- `literal_names`: `.rodata` literals (strings, float constants) become extern references to the target's named data.
- `offset_names`: `B+K` becomes the named symbol at that address.

`probe_relocation.py --only-relocation` → `relocation-probe-only/summary.json`:
- 13 improved, 7 of them to **score 100.0**: both entry-fee panels, both challenge-label functions, func_8005AE1C,
  func_8005DB3C and waitForTitleDemoRaceIntroStart.
- 1 worse, 1 did not compile (`offset_names` on updateRaceSplitscreenSelectPortrait), 13 produced no variant.

**None is object-exact, and the byte certificate shows this is a verification limit, not a source problem**
(`certificate_probe.py`):
- The annotated target dump names `.rodata` references by their `target.s` labels. The byte certificate compares real
  relocations, and there the target relocates against its own `.rodata` / `.late_rodata` sections. The literal
  source matches the certificate's relocation targets; the extern source matches the dump. Neither passes both.
- With the literal source, the remaining object differences are section layout rather than code:
  - IDO pads the candidate's `.rodata` to 16-byte alignment (16 bytes) against the asm-built target's 4-byte
    alignment (8 bytes);
  - the target keeps floats in `.late_rodata`;
  - one relocation flag field differs.
  These disappear only in a translation-unit or ROM build.
- Consequence: functions with their own rodata (strings, float constants, jump tables) cannot be certified by
  isolated per-function object comparison. They need translation-unit-level integration and whole-ROM verification.
  That is a verification design change, not a generator.
