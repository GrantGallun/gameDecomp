# Integrated investigation pilot: September 10

Both exposed development functions started from the same saved C in each arm.
Each arm had a three-logical-call cap and 1800 output tokens per call, with
one transport attempt and a 45-second socket timeout. Arm order was rotated.
The same configured base seed was used, but the existing controllers derived
different effective per-call seeds. This version does not claim an identical
per-call seed schedule; the current pilot code now passes an explicit schedule.

| Function | Arm | Calls | Recorded tokens | Seconds | Score | Exact |
|---|---|---:|---:|---:|---:|---|
| updateControllerPakFileDeleteErrorPrompt | proposal | 2 | 0 | 94.682 | 99.722 | no |
| updateControllerPakFileDeleteErrorPrompt | investigation | 3 | 42 | 25.145 | 99.722 | no |
| initFallingActionProjectile | investigation | 3 | 45 | 94.566 | 99.286 | no |
| initFallingActionProjectile | proposal | 2 | 0 | 96.173 | 99.286 | no |

Neither arm produced an exact object or an advantage over the other arm.
For initFallingActionProjectile, both improved from 99.127 to 99.286 through the
existing `parameter-call-byte-units` normalization, with identical best source
hashes. This is not an investigation-specific gain. Recorded tokens exclude
unreported work on timed-out requests. Proposal arms encountered transport
failures; the shared model server was also serving the existing campaign.
These timings do not establish a speed advantage or isolate scheduler quality.

The investigator performed header investigations, but did not exercise the new
binary-evidence/probe actions in this pilot. It could finish after unsuccessful
header lookups. The main tree now requires target-evidence or diff inspection
before finishing and supplies the investigation question even outside campaign
dispatch. Those follow-up changes are covered by regression tests, not by this
unchanged frozen pilot. The binary-evidence and compiler-probe tools were tested
separately against the real project/toolchain in the compiler smoke receipt.

Sources, private databases, compiler artifacts and model receipts are retained.
No reference function bodies were supplied; project headers are assistance.
No game source integration was performed. The earlier v1 pilot is explicitly
marked interrupted after transport timeouts, not counted as a completed pair.
