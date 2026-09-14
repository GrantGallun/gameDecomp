# Hosted agent vs local model on the 81-200 tier: status

Preregistration: [PREREGISTRATION.md](PREREGISTRATION.md). Runner:
[run_ab.py](run_ab.py). Machine-readable: `summary.json`.

**Nothing is established yet.** Two of six functions have a valid trial on one
arm, and the control arm has not run at all, so there is no comparison here --
only plumbing that works and two data points.

## Valid trials (treatment arm, gpt-5.3-codex-spark)

| function | insns | exact | best score | calls | compiling children | invalid proposals |
|---|---|---|---|---|---|---|
| initControllerPakRaceRecordSaveFlow | 115 | **no** | 100.0 | 7 | 4 | 0 |
| updateCourseSelectCourseDescription | 126 | **no** | 99.84 | 7 | 4 | 2 |

`initControllerPakRaceRecordSaveFlow` reached a weighted progress score of
**100.0 without being byte-exact**, which is the prompt's own warning made
concrete: the score is a diagnostic, not proof, and three separate children sat
at 100.0 while the object still differed.

The model does engage with the task -- it proposed declaration, temporaries and
type-width hypotheses, and four of seven proposals compiled. That is activation,
not capability: zero exact on two functions says nothing either way.

## Voided, not tested (4 of 6)

The account's 5-hour Codex-Spark quota was exhausted part-way through the
cohort. `updateRacePlayerMode37AerialTrick`, `serviceRumbleMotorRequest`,
`alLoadParam` and `packFixedTransformMatrix` recorded two empty responses each
and are **voided**: a spent quota is not a model result. They are excluded from
the numerator and must be rerun, not skipped.

This is worth recording because of how it presented. The CLI prints the limit
message on **stdout** and exits 1, so a shim reading only stderr sees a blank
failure, and the kernel logs `empty-response` -- byte-identical to what it logs
when a model genuinely declines on a hard function. Left alone, this cohort
would have reported "the hosted model failed on four of six medium functions."

Three bugs of exactly that shape appeared while bringing the arm up, all now
tested in `tests/test_codexprovider.py`:

1. **Locale encoding.** stdin was encoded with the Windows codepage, so one
   character outside cp1252 anywhere in a diff made the CLI reject the prompt
   unread -> `empty-response`.
2. **Schema rejection.** The CLI takes only the strict JSON-schema subset and
   rejects the kernel's proposal schema outright -> `empty-response`. The shim
   now retries unconstrained and records `schema_fallback`.
3. **Tool audit blind spot.** The contamination check matched a flat event shape
   this CLI version does not emit, so it would have certified a clean run while
   the agent shelled out. It is now tested against a captured tool-using trial.

Contamination status on the two valid trials: `tool_call_count` zero on every
call. Neither trial executed a command.

## Operational note

The control arm must not load its own copy of the model. gpt-oss:20b is ~11.9 GB
and the card is 16 GB, so a second resident copy spills through system RAM and
locks the machine -- this happened on 2026-09-11 when the control arm used the
default 11434 endpoint while the game-wide campaign held the model on 11435.
`run_ab.py` now refuses to start a local arm when a second copy is resident, and
otherwise joins the endpoint that already has one. Local arms also run with
`SOLVER_GAP_MS=250` so the desktop stays usable.

The treatment arm needs no GPU at all, which is why it can run beside the
campaign.

## Resuming

After the quota resets, from the repository root under WSL:

```sh
python3 eval/results/codex-ab-20260911/run_ab.py --arms treatment   # 4 remaining
python3 eval/results/codex-ab-20260911/run_ab.py --arms control     # all 6
```

Voided runs are retried rather than skipped. The control arm should be run when
the GPU is otherwise idle, so its results are not confounded by queueing behind
the campaign or another experiment.
