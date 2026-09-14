# Closed compiler-helper execution

The routing audit found 32 functions stopped at candidate-only `__ll_lshift`
calls and two at `__ull_rshift`. `Panel` previously loaded only the target's
callees; compiler lowering could introduce additional helpers in a candidate.
The basic leaf runner also lacked the required 64-bit shift instructions.

The implementation admits four fixed discovery names (`__ll_mul`,
`__ll_lshift`, `__ll_rshift`, `__ull_rshift`) before target exploration. Names
provide no authority. Each admitted helper must match an entire closed
word-pair instruction stream, and existing admission checks authenticate symbol
mapping, ROM instruction bytes, and independent reassembly. Candidate-only
discovery entries with unrelated supported leaf code are excluded. Explicitly
supplied environments remain unchanged.

The closed runner implements left, logical-right and arithmetic-right shift,
including low-six-bit counts, 64-bit truncation, and the actual o32 high/low
return registers. It does not admit arbitrary 64-bit programs. Binary/source
return-width diagnostics now identify shift results correctly.

Real replay also exposed a previously hidden simulation error: executing a
concrete helper advanced the ordinal used to generate later opaque-call results.
Equivalent inline arithmetic and helper calls consequently received different
simulated external returns. External `CallEvent.ordinal`, explicit return
overrides, output models, deterministic clobbers and generated stress inputs now
all use the opaque-call trace index. Concrete calls retain separate dynamic
indices for diagnostics. Mixed concrete/intervened execution remains unsupported;
an opaque intervention cannot accidentally replace a helper. Panel policy is
explicitly versioned `unknown-direct-argument-only-inconclusive-v2-opaque-call-ordinals`;
the runner hash also changes. Prior pinned receipts retain their prior meaning.

## Measured real replay

`composeFixedTransformTranslation`, private snapshot attempt **43526**, retains
the same compiler/frontend-passing source and the same 64 target-led inputs.
Target executions are identical between the paired environments:

| Environment | Passed | Inconclusive | Failed |
|---|---:|---:|---:|
| Target callees only | 0 | 64 | 0 |
| Authenticated fixed helper closure | 64 | 0 | 0 |

This is finite diagnostic execution under the recorded environment, not a
source repair, exact-object result or universal semantic proof. Existing opaque
callee and exploration debt remain visible. The initially attempted replay
before the ordinal correction produced 64 artificial failures; that receipt is
preserved under `*-before-opaque-index-fix.json` rather than discarded.

`calculateFixedAngleFromDeltaXZ`, attempt **33441**, has only nine retained cases
under the corrected, newly generated panel; all nine pass in both environments.
The regenerated simulated opaque results change which target inputs complete.
This result does not establish a 56-path recovery from its older 64-case panel.

Alternating admission timings after warmup were approximately 80–95 ms for the
target-only set and 90–105 ms with the closure, roughly 10–17 ms additional work
per panel. The compose panel took 0.82 s. No persistent admission cache or weaker
ROM checks were added.

## Validation and integration

315 focused tests pass, including 180 independent inline 32-bit versus helper
shift boundary cases, real ROM admission/reassembly, altered-stream rejection,
explicit environments, neutral helpers before opaque calls, explicit overrides,
generated stress mutations, output models, and intervention collision refusal.
Root owns final full-suite validation and deployment.

Changed main files:

- `solver/callee_execution.py`: closed streams, admission properties, runner,
  manifest and source-contract diagnostics. Main and frozen were identical
  before this task; whole-file staging is appropriate.
- `solver/mips_differential.py`: TestCase ordinal documentation, Runner counter
  documentation, concrete runner selection, and `_call` opaque indexing.
  Shared-blocker dedup changes elsewhere in this file belong to the other agent.
- `eval/semantic_lane.py`: fixed pre-exploration closure and policy version.
  Root also stages the already implemented branch-context changes in this file.
- `tests/test_word_pair_shifts.py`: all new regressions.

`replay-shift-helper.py` reads a closed prior private history snapshot into a new
private history/workspace and logs revalidation lineage there. It does not copy
the live campaign database, modify canonical game source, or request model work.
Full paired receipts retain source hashes, attempt IDs, exact cases, complete
rows, runtime hashes, admission reports, panel identity and timings.
