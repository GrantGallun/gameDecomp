# Protocol H16: when does `if (!x);` count as a read of x? (written before probe.py ran)

Motivation: the allocator inverter's `raise` operator (eval/results/alloc-inverter-20260923) performed its action in
2 of 30 measured candidates. On the blocked ranges, save stayed flat while span grew by 2 per empty test, so priority
fell. 7 of 9 blocked sites followed a constant or copy assignment (`= 0x100`, `= arg1`); the known fold rule
(uopt53-empty-test-adds-blocks) covers constants. `temp_v0 = reserveSoundEffectQueueWriteIndex(); if (!temp_v0);`
also added no save and is unexplained.

Paired synthetic compiles (traced 5.3 uopt, game recipe). Base, per initialiser E:

    void syn_f(int n) { int x; x = E; [if (!x);] syn_a[1] = x; syn_g(n); syn_a[2] = x; syn_a[3] = x; }

Measured: x's save (adjsave x units(span)) with and without the probe, via allocator_interventions.ranges_by_offset.

H16 predicts the probe adds +1 save to x iff E is neither a constant nor a plain copy of another variable:

| E | predicted delta save(x) |
|---|---|
| `syn_a[n]` (load) | +1 |
| `5` (constant) | 0 |
| `n` (copy of the argument) | 0 |
| `n + 1` (computed) | +1 |
| `syn_g(n)` (call result) | +1 |

Confirmed if all five match. If x has no single coloured range at offset -4 in either compile, that row is
**untestable**. A `syn_g(n)` miss (0) would reproduce the unexplained temp_v0 case and is reported as such, not
fitted.

## Result (results.json): 3/3 testable match, 2 untestable -> not confirmed

Load, computed and call-result initialisers: +1 save and +2 span each, as predicted. Constant and copy: x had no
range at all (uopt propagated it into its uses), so those rows are untestable in a straight-line base. The call row
did not reproduce the temp_v0 case; that one stays unexplained (candidate difference: x here is live across a
call, temp_v0 there is not; not tested).

## H16b (pre-registered): constant and copy with x assigned on two paths

    void syn_f(int n) { int x; if (n) { x = E; [if (!x);] } else { x = syn_a[n]; } syn_a[1] = x; syn_g(n);
                        syn_a[2] = x; syn_a[3] = x; }

Predicted delta save(x) from the probe: E = `5` -> 0 (folded), E = `n` -> 0 (copy propagated), E = `n + 1` -> +1
(control). Confirmed if all three match; a row with no range at -4 is untestable.
