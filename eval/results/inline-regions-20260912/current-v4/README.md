# Pinned target assembly region audit

Checkpoint 5351; diagnostic only. No C source, model requests, builds, live
database copies, status imports, or campaign changes were used.

The collector visited all 2,051 campaign functions. It retained 2,003 assemblies
whose raw target.s bytes match checkpoint pins and whose annotated entry address
matches the checkpoint function. The other 48 lack a pinned target.s workspace
file, including SDK and compiler assembly helpers; each is listed explicitly in
report.json. Original raw bytes are under targets/, with extracted bodies and
both hashes in assemblies.json. Checkpoint pointers and implementation hashes
bind the audit to its inputs and implementation.

With eight-instruction windows and a large-function threshold of 128 instructions:

- 382 available functions are large.
- 1,686 repeated signatures qualify; the report retains its top 100 patterns.
- The strongest signature occurs in 39 functions and 70 nonoverlapping windows.
  Its constants and memory stores resemble graphics command construction.
  This is an interpretation of assembly, not proof of an original macro/helper.
- **Zero strict embedded helper-body candidates were found.** The narrow rule
  requires an entire eligible integer leaf body followed by jr ra / nop; it does
  not establish that the program has no inlined helpers.

The actual prompt component renders on these largest qualifying functions:

| Function | Instructions | Within-caller patterns | Prompt characters |
|---|---:|---:|---:|
| resolveRaceCourseSurfaceCollisionWithNormal | 1,071 | 4 | 534 |
| resolveRaceCourseSurfaceCollisionWithVelocity | 1,061 | 4 | 534 |
| updateRaceResultsFlow | 933 | 14 | 713 |

For example, updateRaceResultsFlow has matching eight-instruction shapes at
zero-based instruction indices 279, 308, 792 and 823. prompt-examples.json retains
the rendered bounded prompts, exact input hashes and witnesses. The prompt says
to compare source forms and repair one occurrence at a time, preserve effects,
avoid introducing absent target calls, and verify the entire caller. It grants
no correctness verdict and was not sent to a model by this audit.

Run from the repository root in WSL:

```text
python -m eval.inline_regions --run eval/results/resume-pipeline-20260908 --out NEW_DIR
python -m eval.inline_regions --assemblies eval/results/inline-regions-20260912/current-v4/assemblies.json --out NEW_REPLAY_DIR
```

The actual offline CLI replay in ../replay-v1 used only these retained inputs;
../replay-validation.json records identical input bytes and analysis JSON.
Collector/algorithm tests passed together: 15 tests. Earlier current-v1/v2/v3
directories contain only frozen pointers from unsuccessful read-only attempts
while the checkpoint database required journal recovery; they are not results.
