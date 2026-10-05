# Protocol: frame-too-big -> inline a declared local (written before frame_population.py ran)

Origin: finishCurrentRdpTask (frame 0x28 vs target 0x20). Inlining the register local `temp_v0` gave frame 0x20
and an exact object; two rounds of solver.frontend_type_repair (prototype, then the new parameter-type rule)
passed the gate; recorded independently (receipt 95886, 377 -> 378, project-header-assisted).

**Anomaly, not explained by ido53-frame-layout:** sp1C is the only memory local (-4), yet the locals area was 16
bytes; with the declarations swapped (sp1C at -8) it was still 16. The H15c depth clause predicts 8 in both. So a
register local can own frame bytes; when is not known. No rule is fitted to this one function.

Population test (a generator trial, not a rule test). For every unsolved function whose best node's frame is
LARGER than the target's (restart round 3, frame_census.json): one candidate per declared local with exactly one
assignment and no call in its value (alloc_inverter._inline), cap 8 per function; compile each via the
population probe driver; a score-100 object the frontend gate rejects gets up to two rounds of
frontend_type_repair. Reported: functions fired on, candidates, candidates whose frame now equals the target's
(the action), score up / down, exacts. Predicted (weakly, n=1 origin): the action happens in some, and exacts are
rare (most frame functions also carry non-frame residual). Install as a pipeline generator only if the action
rate is non-trivial and a paired population rerun loses 0 exacts.
