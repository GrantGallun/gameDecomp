# Protocol: what gives a uopt live range its preferred colour (IDO 5.3)

Written before `probe.py` ran.

The 5.3 selection model (solver/uopt_trace.py, 99.9% of 11,310 census decisions) takes a range's preferred colours
(last column of its level-5 block rows) as given. To change a register choice, the inverter must know what CREATES
a preference. Colour numbering: 1 v0, 2 v1, 3 a0, 4 a1, 5 a2, 6 a3, 7.. temporaries, 14.. saved.

Hypotheses (paired synthetic compiles through the traced game recipe; local `x`, one M range):
- P-arg: passing x as call argument k gives x a preference for a(k) (colour 3 + k) in the block of the call.
- P-ret: returning x gives a preference for v0 (colour 1) in the returning block.
- P-callres: assigning a call's result to x gives a preference for v0 in the block of the call.
- P-none: x used only in arithmetic and stores has no preference.
- P-param: a parameter keeps its incoming register as a preference (already in the model; checked).
Each confirmed if the predicted colour appears in x's preference list in every variant tested (k = 0..3 for
P-arg); the observed preference lists are recorded either way.
