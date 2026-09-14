# Stress input diversity

The stress panel compared mutation histories instead of final input assignments.
Appending a value already assigned to a field could consume another case slot.
The implementation now ignores earlier fully shadowed exact-location/exact-width
writes when comparing stress inputs. It retains the original serialized cases,
survivor write order, overlapping widths, distinct alias names, seed, registers,
and call returns. Coverage exploration and all verdict rules are unchanged.

## Real private replay

`probe-input-dedup.py` replays the existing private
`drawCharacterSelectCourseRecordsPopup` target and unfinished guard candidate.
It performs no model calls, live database reads, or canonical source changes.
The baseline restores only the previous stress identity function in memory.
`input-dedup-probe.json` records the before/after results and runner hash.

| Metric | Previous | Deduplicated |
|---|---:|---:|
| Selected cases | 64 | 64 |
| Distinct conservative input identities | 60 | 64 |
| Executed target trials | 65 | 65 |
| Returned selected target cases | 64 | 64 |
| Distinct observed target call sequences | 14 | 15 |
| Covered target instructions | 225 | 225 |
| Covered target branch edges | 21 | 21 |
| Unfinished candidate failures | 64 | 64 |
| Executed target instructions | 71,985 | 77,166 |

This improves diversity at the same case/trial budget. It is not a wall-time or
coverage gain: replacing duplicates with different valid cases performs more
useful instructions, and this replay took about 1.50 versus 1.68 seconds for
stress selection. All existing noncompletion and finite-input debt remains.

The final replay includes the opaque-call ordinal correction and closed shift
helper support; runner SHA256 is
`61ca9a6552b3e13fb64f72a9f19a66aee294d5ed94092c08e9966f6353659f66`.
The earlier independent receipt is preserved as
`input-dedup-probe-before-opaque-ordinal.json`. The combined release preserves
the60-to64 input diversity improvement and14-to15 call-sequence improvement;
its different deterministic opaque environment lowers both arms' instruction
counts by5,176. Neither arm changed coverage or the unfinished candidate verdicts.

72 focused tests passed (`test_differential_input_identity.py` and
`test_mips_differential.py`), including overlapping write order, possible alias
order, unchanged raw history, differing environment inputs, target self-replay,
and rejection of a wrong candidate.

## Rejected hypotheses

At the same 256-case popup exploration budget, prioritizing fault repair yielded
239 faults/17 returns versus baseline237/19. Rotating memory dimensions yielded
236/20. Both kept the same225 instructions/21 edges. Neither ordering change was
adopted.

Applying shadow-write dedup to coverage search at the existing5000-case ceiling
and1,280,000-instruction budget also left coverage unchanged (510 instructions,
33 edges). Its retained search trajectory changed, so it was not adopted.
`fault-search-probe.json` preserves that last experiment; its prototype script
is separate from production source.
