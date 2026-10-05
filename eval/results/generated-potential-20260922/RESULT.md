# Generated capability potential: September 22, 2026

Implemented bounded reachability over `requires`, `produces`, `preserves` and
`invalidates` contracts. The resulting paths determine each operation's
`potential`; combinations do not need entries in a preset capability table.

Primitive specifications and the reviewed caller declarations remain authored.
The engine computes their conditional compositions. It does not discover arbitrary
Python semantics or prove that a declared contract is correctly implemented.

## Test results

**340 tests passed on Windows and 340 on native WSL**, including 24 generated
potential tests. The suite includes existing capability, theory, repair, replay,
search and relevant constructor tests. The same frozen implementation bytes were
loaded on both platforms.

The positive symbolic test starts with a measured layout but incompatible fields.
Two separately declared operations generate the previously unlisted composition:

```mermaid
flowchart LR
  A[Layout known; named fields absent] -->|name_fields| B[Layout preserved; named fields available]
  B -->|lift_wide| C[Wide candidate goal]
```

This diagram is a synthetic acceptance example. It does not assert that its
`name_fields` conversion is implemented in the real solver. A separate adapter
test uses the existing `type_layouts` and `wide_operations` contracts and controlled
input facts to generate their two-step path.

Tests also establish that false prerequisites block a transition; unknowns remain
explicit conditions; incompatible branches cannot pool their facts; mutations
discard stale source-bound feedback; assumptions cannot be preserved or directly
reasserted into goal achievement; bounds remain visible; altered derived reports
are rejected; CLI exports refuse overwrite; and opt-in planner replay uses the
same compiler-call count.

Independent review found one interpretation edge around direct self-reassertion
of an assumed goal. Its regression failed before the provenance fix and passed
afterward; a focused follow-up review found no regressions.

## Application to retained timer evidence

The new run reuses three selected source-bound assessments from the
[previous capability audit](../capability-envelope-20260922/RESULT.md): receipts
90, 94 and 100. It generates one report for their original theory caller and one
for the declared compile-recovery caller. The latter is explicitly a hypothetical
route change, not an executed repair campaign or refreshed workspace observation.

| Retained state | Generated result | Action supported by the result |
|---|---|---|
| `osSetTimer`, receipt 90 | The compile-recovery model generates `type_layouts -> wide_operations` | Establish layout-probe availability, type-plan domain support and the closed wide idiom before testing the construction |
| `__osInsertTimer`, receipt 94 | No scalar-member path is found in either assessed caller within the model/bounds; wide-return repair is disconnected from both callers | Keep the indexable-base discrepancy and missing wide-return route explicit |
| `__osTimerInterrupt`, receipt 100 | Direct named-field wide reconstruction is blocked by the byte-view shape; longer compile-recovery paths are conditional on the new draft's shape and other prerequisites | Inspect the intermediate representation; no compatible conversion has been demonstrated by these paths |

Wide reconstruction remains disconnected in the original theory caller. The
alternate caller does not erase domain guards. Object-check goals have no path
for these noncompiling candidates because no successful-compile transition is
invented. An actual compile and new source-bound assessment are required.

All six reports use depth 3 and a 256-node cap. They contain 104 to 213 nodes;
none hit the node cap, and all retain a depth cutoff. Multiple paths may reach
the same goal or differ only in intermediate observations. Their number is not
a count of independent discoveries, new capabilities or improved matches.

Reports: [SetTimer/theory](analysis/osSetTimer--theory.json),
[SetTimer/compile-recovery](analysis/osSetTimer--compile-recovery.json),
[InsertTimer/theory](analysis/__osInsertTimer--theory.json),
[InsertTimer/compile-recovery](analysis/__osInsertTimer--compile-recovery.json),
[TimerInterrupt/theory](analysis/__osTimerInterrupt--theory.json),
[TimerInterrupt/compile-recovery](analysis/__osTimerInterrupt--compile-recovery.json),
[symbolic composition](analysis/synthetic-chain.json).

## Evidence and boundaries

This task made **zero new compiler calls**. Generated states remain contract
inferences with path-local assumptions, not observed C outputs, exact matches or
all-input semantic proofs. No main KB, production translation unit, model weight
or acceptance rule changed. All new reports remain training-ineligible.

The exported paths bind the retained assessment, source, target, compiler,
assistance and caller. Prior owner references remain those of the input catalogue;
the generator and adapter have separate current-code identities. The run does not
claim those historical assessments reflect an independently refreshed workspace.

The separate audit reconstructs all six generated reports, verifies the synthetic
path, binds prior input files to their original audit hashes, and checks 868 frozen
Python files plus 37 test-only fixtures against the current workspace. Neither
the generator nor the timer analysis uses reference implementation bodies.

Artifacts: [run report](analysis/report.json), [audit](analysis/audit.json),
[freeze](freeze.json), [Windows tests](tests-win32.json), [WSL tests](tests-linux.json).

## Use

```text
python -m eval.capability_potential --assessment FILE --node root --output NEWFILE --max-depth 3 --max-nodes 256
```

`TheoryOnline` can retain the same generated reports with
`capability_assessor=callback, capability_potential=True`. The option defaults off
and changes neither action ordering nor compile budgets. See the
[API and mathematical semantics](../../../docs/CAPABILITY_ENVELOPE.md).

To repeat the scoped verification in the existing environment:

```text
python eval/results/generated-potential-20260922/verify_tests.py
wsl.exe -d Ubuntu -- /home/grant/decomp/sbk1/.venv/bin/python /mnt/c/Code/gameDecomp/eval/results/generated-potential-20260922/verify_tests.py
wsl.exe -d Ubuntu -- /home/grant/decomp/sbk1/.venv/bin/python /mnt/c/Code/gameDecomp/eval/results/generated-potential-20260922/audit.py
```

The frozen run refuses to overwrite its analysis or refresh its code after results
exist. Use a new revision directory for changes.
