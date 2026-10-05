# Three byte-exact matches, and a defect in this tool that hid the band's real rate

Run 2026-09-16/17. `eval/close_nearmiss.py`, deterministic, no model, no GPU.
State: `state.json` in this directory. 105 of 106 functions processed.

## Result

| function | start | closed by | compiles |
|---|---|---|---|
| `updateRacePlayerMode07LaunchRampPose` | 98.393 | `field_local:temp_t9:compound` | **1** |
| `updateEndingTommyWaitThenFinalPhase` | 99.744 | `decl_order:1` | 4 |
| `reserveSoundEffectQueueReadIndex` | 98.692 | `commutative:==@470` | 11 |

All three independently re-verified by recompiling the stored `exact_source` through the oracle in a
fresh call: `compiled=True exact=True score=100.0`, byte certificate `exact: True`.

`python -m eval.status` after: **byte-exact 209 -> 212, SOLVED 143 -> 146**, header-assisted 11 and
recovered 55 unchanged. The ratchet did not decrease.

## The measured band rate, after correcting this tool

Two earlier versions of this file carried wrong numbers, and both errors were in the tool rather than
in the census it was testing:

- "3/20 in-band" -- a partial run, quoted before the sample existed.
- "4.7%; the census's 68% does not transfer" -- **an artifact.** `close_one` checked only the first
  half of the census band (`dominant == regalloc`) and omitted the second half (at most two other
  faults). It therefore searched 51 functions the census had already measured as 0-of-16, at an
  average of 258 compiles each.

The band, measured over the 65 functions actually searched:

| reading | n | closed | rate |
|---|---|---|---|
| in band (regalloc, others <= 2), BASELINE profile | 14 | 3 | **21.4%** |
| in band, AFTER-LAYOUT profile | 14 | 3 | **21.4%** |
| outside the band under either reading | 51 | 0 | 0.0% |

Both readings agree exactly, so the corrected figure is not itself a definition artifact.

```
other_faults 0    n=6   closed 2   33.3%
other_faults 1    n=6   closed 1   16.7%
other_faults 2+   n=53  closed 0    0.0%
```

**Every closure had at most one other fault.** So the census band does transfer; this tool was
diluting its own result by searching outside it, and in doing so spent **13,156 compiles** on
functions it should have declined. The guard now enforces both halves, with `--force-regalloc` and
`--max-other-faults` to override deliberately.

The honest claim: **the band is worth searching at roughly 1 in 5, and nothing outside it closed.**

## The campaign's in-band residue

Pointing the same tool at the campaign database found **46 pending functions that are
register-dominant with at most two other faults and have never had a regalloc search.** Searched so
far: **0 of 17 exact**.

This population WAS band-filtered correctly (`others <= 2` in the census query), so the corrected
rate above does not explain it. The expected shape if the census's band really is near-exhausted:
what remains in-band in the campaign is residue that has survived previous work, unlike the research
KB's untouched set. A 46-function opportunity was worth testing; 0/17 so far says the yield there is
much lower than 1 in 5.

## What this does not establish

- 105 of 106 processed; the campaign run is 17 of 46.
- The three matches are SOLVED by `eval/status`'s criterion. Their candidate sources should still be
  checked for the include-route header assistance documented in `eval/status.py`; that check has not
  been run across the matched set.
- Attempts from the campaign run were written to the campaign database, which was paused and not
  otherwise modified. No checkpoint, state, or `functions` row was touched.

## Reproduce

```bash
wsl.exe -e bash -lc "cd /mnt/c/Code/gameDecomp && python3 -m eval.close_nearmiss \
  --out eval/results/close-nearmiss-20260916 --budget 300"
```
