# Final pointer-allocation tie probes

Two final probes completed the40-build selector branch (including its baseline). Neither exceeded the retained98.511 score.

`00.c`, **98.156**, removes the named index and constructs the allocated node pointer before the predecessor pointer. This fixes every persistent register assignment: allocated node v1, predecessor a3, count address t0. Opcode sequence and instruction order remain correct. The remaining difference is a consistent one-slot-earlier temporary-register cycle, beginning in the selector prologue and continuing through counter decrements and list handling. This is a useful backend diagnostic candidate, not an accepted score improvement.

`01.c`,92.411, initializes the predecessor before the free-count guard. It regressed.

The diagnostic98.156 candidate has now been independently replayed: **76/76 cases pass**, zero failed or inconclusive cases. See `replay.json`. It is preserved as a semantic-clean alternative with correct persistent register assignment; the98.511 score champion remains retained separately.

Across all40 builds, no new candidate was retained and no production search family was added. Source relationship generator tests:3passed. The existing98.511 semantic-clean champion remains unchanged.
