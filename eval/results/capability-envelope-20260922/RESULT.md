# Capability envelope: September 22, 2026

Implemented a map of what 18 existing components should produce under their
declared domains and prerequisites. Expected capability is independent of observed
success; a negative result creates a specific investigation rather than closing
the whole goal. The catalogue is a reviewable subset, not a proof of complete
decompilation capability.

Read the [actual map](analysis/MAP.md), [contract catalogue](analysis/catalog.json)
or [API and interpretation guide](../../../docs/CAPABILITY_ENVELOPE.md).

## Observed results

The read-only audit reassessed **100 existing compiler observations in 30 worlds**
(ten functions, three prior experimental arms). It made **zero new compiler
calls**. The original seven independent confirmation compiles are separate from
these 100 observations; their prior audit is preserved in the
[theory experiment](../repair-theory-20260922/RESULT.md).

Seven functions retain concrete exact-C witnesses in each arm. These are known
results, not newly recovered matches or a current global project count. The
three header-assisted timer drafts remain unresolved:

| Function | Finding | Action justified by the map |
|---|---|---|
| `osSetTimer` | Public signature shape is outside the current signature constructor; the single-wide-parameter repair also cannot express this mixed signature | Develop or select a constructor that covers this ABI shape; do not expect a retry of either restricted constructor to fix it |
| `__osInsertTimer` | Receipt 94 to 95 rewrites scalar member access despite the intended requirement for an indexable base; four member errors become four indexing errors | Investigate the scalar-member generator's domain guard and the intended representation |
| `__osTimerInterrupt` | Receipt 99 to 100 clears twelve member errors and leaves one call error; the resulting byte-view draft is outside the direct named-field wide-operation grammar | Investigate representation composition and access to the broader recovery path |

Wide-operation reconstruction exists in `compile-recovery` but is not connected
to the theory caller. Its measured-layout and closed-idiom conditions still need
establishing. The broader recovery path can first construct a fresh header draft
and measure layouts; connecting the direct wide repair to an arbitrary byte-view
child alone does not meet its contract.

The scalar-member discrepancy is reported as `outside-contract-construction`,
not a proved implementation bug. No generator behavior was changed by this
observer task. Constructor failures to emit despite satisfied prerequisites are
also detected and tested, but this recorded panel contains no such conflicts.

## What the ceiling means

The catalogue separates candidate constructors, analysis components, finite
behavioral checkers and the exact-object checker. A correctly implemented checker
cannot supply a missing constructor. Correct model inference does not provide
perfect weights or exhaustive search. Local component coverage therefore does
not establish a whole-function exact-match percentage.

The remaining whole-function construction/composition/search obligations are
explicit. Failed candidates do not establish impossibility. An actual exact
certificate is a positive reachability witness; keeping the original binary is
a separate preservation capability and does not count as reconstructed C.

The instruction inventory currently does not expand assembler macros. Macro
definitions and `nonmatching`/`endlabel` wrappers in this panel leave instruction
coverage unknown. This is a limitation of the inventory, not a finding that the
corresponding CPU instructions cannot be handled. Accordingly, draft construction
is not overclaimed from this inventory. Unmeasured layouts and unsupported
predicate checks remain unknown as well.

## Verification and provenance

- **316 tests passed on Windows and 316 on native WSL**, including 26 new capability
  tests and the existing theory, repair, search and relevant constructor suites.
- The initial WSL collection attempt lacked fixture files. The snapshot packaging
  was corrected and the full scoped suite passed. The final fixture manifest
  contains 37 existing test assets; generated before/exact pairs are test-only
  fixtures and are not input to the capability assessment.
- The frozen native implementation contains 864 Python files. Catalogue references
  pin owners, tests and actual caller files. Both test receipts identify the same
  loaded implementation bytes.
- The assessment driver validates prior frozen code/artifacts, read-only database
  receipts and compiler/header/assembly identity before and after each workspace
  assessment. Every report retains source, target, compiler and assistance context.
- The separate export audit reconstructs all 100 assessments, parent/child
  comparisons and recorded proposal comparisons, checks the exported summary and
  map, and binds the test receipts and native/current source identities.

Artifacts: [freeze](freeze.json), [Windows tests](tests-win32.json),
[WSL tests](tests-linux.json), [fixture manifest](test-fixtures.json),
[report](analysis/report.json), [export audit](analysis/audit.json).

The optional planner callback retains diagnostic assessments without changing
default output, repair order, compile budgets or acceptance. Main KB, production
translation units, model weights and prior frozen experiments were not changed.
Reference implementation bodies were not used by the assessment. All new
assessment artifacts remain training-ineligible.

To reproduce verification in the existing native environment:

```text
wsl.exe -d Ubuntu -- /home/grant/decomp/sbk1/.venv/bin/python /mnt/c/Code/gameDecomp/eval/results/capability-envelope-20260922/verify_tests.py
wsl.exe -d Ubuntu -- /home/grant/decomp/sbk1/.venv/bin/python /mnt/c/Code/gameDecomp/eval/results/capability-envelope-20260922/audit.py
```

`run.py` generated the preserved analysis from the frozen implementation and
refuses to overwrite its output. Use the CLI with a new output directory for
another analysis; do not refresh the freeze after results exist.
