# One assignment-order probe: rejected

The trace narrowed the 99.889 residual to two independent `0x80` loads exchanged
across the same branch's delay slot. The `split` census label described mixed
target-register votes under positional alignment; it did not establish a target
compiler range-splitting operation. See [TRACE.md](TRACE.md).

The prediction in [PROBE.md](PROBE.md) preceded one source proposal and compile.
Only the two first-arm assignments in retained attempt 157771 were reversed.
The parent was ordinary private baseline 157772, with verified source hash
`24a97e50...`; the unrelated isolated `base.c` seed was not used as input.

Private receipt **157773** compiled and passed the frontend, but scored
**99.333**, below 99.889, with a nonexact certificate. The initial load order
remained wrong. Later branch assignments, register roles, stack halfword slots
and call-argument loads changed as collateral. The predicted isolated scheduling
effect was falsified for this edit; no candidate was promoted or imported.
[PROBE.json](PROBE.json) records the full diff, certificate and actual parent.

The existing full mutation stream already emits this exact source as proposal
**1**, `stmt_move:7->8`. Adding another assignment-swap generator would duplicate
coverage. The next experiment needs a way to alter the initial delay-slot choice
while preserving the later allocation and storage decisions, with those effects
predicted separately. Do not treat the original `split` label as that explanation.

This follow-up used one ordinary scoring call (one IDO compile plus one frontend
invocation), with a durable attempt and verified parent edge. The preceding
baseline/trace work used one ordinary scoring call plus three IDO trace compiles.
Total: five target-compiler invocations and two frontend invocations. No reference
or winning C was used, and no live state, production generator or game source
changed.
