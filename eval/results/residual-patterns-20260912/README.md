# Current diffs and the next analysis investments

The instruction-pattern proposal has immediate value. The new
`eval.residual_patterns` audit reads one checkpoint-selected source-bound attempt
per function, rather than counting repeated search history. It verifies source
and address bindings, uses short read-only database queries, retains raw diffs,
and preserves the immutable checkpoint pointer. It never sends source to a model.

Checkpoint3707:1153 compiled nonexact functions have usable instruction diffs.
668 exact functions and203 noncompiling functions are excluded;27 other selected
attempts have no usable compiled nonexact instruction diff. All1153 have pinned
target assembly for descriptive opcode baselines. Baseline/object disassembly
dialects can differ, so no misleading opcode-enrichment ratio is calculated.

## What actually repeats

| Observation | Functions | Interpretation limit |
|---|---:|---|
| Register operands differ in single-instruction replacement blocks | 774 | Not proof of register allocation or equivalent values |
| Control operands differ in those blocks | 464 | Labels/addresses alone do not prove different branch semantics |
| Stack-frame adjustment differs | 326 | Local storage, spills and outgoing arguments can all contribute |
| Stack-slot offset differs | 179 | Stack homes are not source struct-field offsets |
| Same `lw` at0x18 with changed destination register | 72 | A recurring short motif, not a repair rule |
| Same `lhu` at0x2a with changed destination register | 64 | Same limitation |
| Same `lw` stack home0x18->0x1c | 23 | Narrow source-shape pilot selected from this group |

Counts overlap. Single-instruction families cover only one-to-one contiguous
replacement blocks; longer blocks are never zipped into supposed corresponding
instructions. N-grams never cross unchanged context or hunk boundaries. Identical
instructions on both sides are counted with multiplicity as textual displacement
candidates, not erased or declared equivalent.

The high raw counts for `addiu`, `lw`, and `sw` mostly identify common MIPS code.
The operand patterns provide the actionable distinction. Existing
`isolated-register-web-source-shape` catalog guidance already covers part of the
register-web hypothesis; a new generic register rewrite would duplicate machinery.

Final report: `current-v3/README.md` / `report.json`; raw source-bound inputs:
`current-v1/records.json`. The replays preserve the same input digest and record
the analyzer/dependency hashes. Seven focused audit tests passed before the final
metadata-only provenance addition; final suite results belong to release notes.

Run a fresh audit (use a new output directory):

```text
python -m eval.residual_patterns --run eval/results/resume-pipeline-20260908 --out eval/results/my-residual-audit
```

Reanalyse a retained audit without accessing live databases:

```text
python -m eval.residual_patterns --records eval/results/my-residual-audit/records.json --out eval/results/my-residual-replay
```

## Repair implemented from the recurring stack-home motif

`rewrites.stack_home_padding_rewrites` adds one source-bound candidate only when
the entire residual is a safely paired load/store of one stack word, the target
offset is eight-byte-aligned, and the candidate uses target+4. It also requires
one supported uninitialized integer local and rejects recognized explicit address
taking, ambiguous/mixed diffs and existing padding. Cheap shape checks precede
the aligner, so unrelated residuals avoid that additional analysis.

The experiment inserts a leading four-byte unused volatile array. Three related
callbacks become object-exact, frontend-valid and pass64 differential cases each.
Register qualification and trailing padding leave all three unchanged; an aligned
union is another successful experimental representation. The final generator,
through the normal proposal API, reproduces all three exact results. It fires on
7 of23 current candidates with the observed stack-home motif. This is narrow
family replication, not proof of broad transfer or the original C declaration.

Catalog provenance: `single-local-stack-home-padding`. Full trial history and
positive/negative outcomes: `stack-home-v1/README.md` and `final-generator.json`.
No private experiment attempts or repaired sources are imported into the live
campaign. `release/deployment.json` and `release/live-validation.json` record
whether the generic generator has been installed and the run resumed.

Installed revision `20260912-stack-home` at checkpoint3886, with670 exact
functions before amendment. Main2543 tests pass (52.11s); frozen2311 pass (45.29s).
Only the generator/wiring and catalog provenance were grafted into live code;
unrelated main-tree rewrites remain outside the frozen release. Previous code
and checkpoint references are retained under the run's revision directory.
Normal service resume uses its existing200-item batches, model and worker
settings. Private exact prototypes and integration receipts remain separate.
Final validation: checkpoint3890, fresh running heartbeat, all16103 pins verified,
one completed work item since amendment, and the frozen normal proposal API fires
on the real motivating source/diff. All recorded checks pass. The live count is
670 exact functions at this check; the three isolated repairs are not counted as
live promotions.

## Pasted recommendations checked against the implementation

Integration is already implemented and historically demonstrated. We used it to
verify the current five pending boundary-exact candidates together: a byte-identical
8MiB ROM,25.72s, with canonical files unchanged. No campaign status import occurred.
The useful next addition is source-bound result reconciliation and integration
orchestration for the fast campaign. See `INTEGRATION_AUDIT.md`.

Runtime capture exists for stopped compatible debugger endpoints and integer
leaves. Project64 is installed, but a compatible capture/export bridge has not
been verified. Capture replay still rejects calls/FPU/64-bit code, even where the
ordinary synthetic Panel has concrete helper support. The smallest meaningful
pilot is one real leaf capture plus self/wrong-candidate replay controls, before
building broad scenario automation or learned call summaries. See `runtime-audit.md`.

The current target explorer already inverts some simple guards. A bounded derived
predicate with preserved earlier guards is a useful next experiment; another
random mutation pass or a new unconstrained symbolic engine is not justified by
the present results. External-game evaluation remains a separate transfer test.
