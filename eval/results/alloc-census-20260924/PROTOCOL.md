# Protocol: allocation census of the 130 structurally-correct campaign functions

Written 2026-09-24 before `census.py` ran. Pool: campaign functions whose best attempt has zero structural steps
(`frontier-20260924/fronts.py`; 72 small, 58 medium). For each, the campaign's best source is compiled with its
recorded recipe (object dump) and through the tracing IDO (`solver.uopt_diagnosis.traced_compile`: -zdbug:5/6 and the
ugen tree), then `uopt_diagnosis.diagnose` classifies every wrong live range: ugen_temp / blocked / selection / split.
Serial (uopt writes its listing into the repo directory). Reproduction check: the rebuilt score must be within 0.01 of
the campaign's recorded best; rows that are not are reported apart and excluded from the reading.

## Reading (fixed now)
Per function, the FIRST wrong range (colouring order; later decisions depend on it).
- blocked + ugen_temp >= 50% of diagnosed: the inverse is edit-reachable. Build the diagnosis-driven inverse for those
  classes (uopt_diagnosis.CLASS_FAMILIES already names generator families per class), aimed at this pool.
- selection >= 50%: the allocator front needs compiler research first (the selection/priority rule, untested); pivot
  to the large-function fault ladder.
- otherwise: mixed; report the classes and the generator families already tried on each.
