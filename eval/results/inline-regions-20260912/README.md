# Inlining machinery, September 12

Implemented binary-only repeated-region audit, bounded within-caller repair-prompt
hints, and explicit expansion of source-local pure integer helpers through normal
differential exactness search. No reference C is loaded. Region similarity does
not establish original compiler inlining, C equivalence or an exact match.

## Real program measurement

`current-v4/report.json` binds checkpoint 5351 and every retained target file.
2,003 of 2,051 functions had pinned target assembly; 48 missing pins are explicitly
reported. Of those available, 382 have at least 128 instructions. The scan found
1,686 repeated eight-instruction signatures and **zero** strict whole-helper-body
matches. Repeated windows can be idioms or macros. The strongest signature appears
in 39 functions at 70 non-overlapping locations and resembles display-list stores.

`current-v4/prompt-examples.json` demonstrates the actual bounded prompt component:

| Function | Instructions | Repeated patterns | Hint characters |
|---|---:|---:|---:|
| resolveRaceCourseSurfaceCollisionWithNormal | 1,071 | 4 | 534 |
| resolveRaceCourseSurfaceCollisionWithVelocity | 1,061 | 4 | 534 |
| updateRaceResultsFlow | 933 | 14 | 713 |

The prompt component was exercised on retained actual target assembly, without
model calls or any claim of a repaired game function. Normal byte and semantic
prompt builders include at most three groups; cross-function audit findings are
not automatically injected as helper C. Reproduce offline with:

```
python -m eval.inline_regions --assemblies eval/results/inline-regions-20260912/current-v4/assemblies.json --out NEW_DIRECTORY
```

## Expansion validation

The actual IDO compiler emitted eight instructions for a synthetic helper-call
caller and four for the new generator's expansion. The latter exactly matched
the synthetic explicitly expanded target's selected function bytes. The helper
definition remains in the translation unit: this is not whole-object equality or
a campaign win. All three compile attempts and sources are retained under
`../inline-expansion-20260912/README.md` and its receipt/native workspace.

The operator supports pure integer return expressions, builtin scalar argument
types and conservative source-local bindings. It does not reconstruct missing
helpers, arbitrary control flow, memory effects, included macros, or original
inline boundaries. Full caller compile/differential/exactness gates remain.

## Checkpoint recovery

The audit exposed a previously stopped campaign: checkpoint save had encountered
`sqlite3.OperationalError: disk I/O error`; subsequent readonly startup could not
recover a hot rollback journal. Under paused service and locks, normal SQLite
read-write opening recovered the journal. Checkpoint 5351 pointer stayed identical,
and its manifest plus all selected object hashes passed validation. No journal was
manually deleted and no status was promoted. Evidence is under
`../checkpoint-recovery-20260912/`. The underlying disk I/O cause is unproven.

Earlier audit directories current-v1/v2/v3 contain failed pre-recovery attempts;
current-v4 is the successful corpus audit. Windows compiler-dependent tests were
unsuitable in the default sandbox; release verification uses the supported WSL
environment. No tests or correctness gates were weakened.

## Release

Installed `20260912-inline-regions` at checkpoint 5356. Full main suite:
**2,722 passed in 59.05s**. Full frozen suite: **2,461 passed in 50.81s**.
Five runtime pins changed; three are new modules. Match counts remained
672 object-exact plus 5 integrated. Normal service resume was issued with all
existing options, including integration and muted runtime captures.

Final `live-validation.json`: checkpoint **5371**, all **16,114 pins verified**,
**3 new repair items completed** after deployment, all checks true. Supervisor772
and worker773 were running with fresh heartbeat; 677 exact/integrated preserved.
This verifies operation after resume, not repair gains attributable to inlining.

The first deployment attempt declined before mutation because the dead
supervisor's service record still said `needs_repair`, despite the pause flag.
Invoking its normal paused branch updated that record, then deployment succeeded.
Both logs are retained (`deploy-precondition.log`, `deploy.log`).

Scoped stage uses frozen originals for the two changed existing runtime modules,
with explicit grafts rather than a wholesale main upgrade. `staged-manifest.json`
binds the exact release files. Deployment requires a paused/drained campaign,
matching pins, model and inventory, and a passing full staged suite. Roll back
only with service paused, restoring old code and the matching checkpoint together.
