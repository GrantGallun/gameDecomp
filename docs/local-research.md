# Local compiler research

Open **http://127.0.0.1:8765** (or double-click `launch-progress.cmd`) and use the
**Compiler research** panel. Choose a local model and limits, then **Start research**.
**Stop research** cancels the active tool call. Campaign pause/resume is independent.
The same page carries a separate **Adapter training** action, described under
[Adapter training](#adapter-training) below.

The default is the already-installed **qwen2.5-coder:14b**, **30 minutes**, **12 model
calls**, and at most **72 compiler invocations**. The first exhausted limit stops
the run. There is no automatic restart, model download, or paid-provider fallback.
Failed requests consume their call allowance. Three consecutive proposal/request
errors end a run early. The selectable gpt-oss:20b timed out in the initial local
canary; the smaller coder is the practical default for this installation.

## What runs

1. Verify that Ollama serves installed local GGUF weights, recording their digest.
2. Resolve the game's actual IDO recipe, recording configuration and tool hashes.
3. Give the local model a deterministic synthetic generator example and a bounded
   notebook of prior experiments under the same recipe.
4. Ask for two small C89 source templates, an explanation, and a mechanical
   prediction about their generated code. Both templates use `{{K}}`.
5. Freeze the prediction. Compile both templates at one discovery value and two
   independently selected confirmation values. Measure the objects using the
   existing `tools.synthetic_corpus` feature extractor. A failed compile or
   counterexample ends the experiment early to save the remaining compiler work.
6. Retain the result, including compile errors and counterexamples, and supply it
   to subsequent proposals. Identical source pairs are skipped.

The model can propose C, but cannot supply shell commands, Python, filesystem
paths, compiler flags, preprocessor directives, assembly, or network destinations.
Generated C is compiled and disassembled, never executed. Compiler children have
CPU, memory, file-size and wall-time limits and run one at a time.

Network requests use only Ollama's native API on loopback or the actual WSL host
gateway, port 11434. HTTP redirects, proxies, cloud aliases and remote metadata
are rejected. No API keys are read. This removes per-token API spending; local
GPU/CPU use still consumes electricity and can compete with other jobs.

## Evidence and state

- Canonical notebook: `/home/grant/decomp/local-research/notebook.jsonl` in Ubuntu.
- Full receipts, prompts, responses, C, object files and assembly:
  `/home/grant/decomp/local-research/runs/<run-id>/`.
- Windows status/control projection: `eval/results/local-research/`.
- A Linux advisory lock prevents concurrent workers, including after dashboard
  restarts. The projection is a view, not evidence or campaign state.

The panel shows the latest run's counts and six most recent experiment outcomes.
Expand a completed experiment to inspect its prediction, source and measurements.
The notebook persists across runs, including findings not shown in this panel.
An interrupted experiment remains incomplete; it cannot become confirmed.

**`synthetic_confirmed` means the stated code-generation measurement held on those
three inputs.** It does not prove semantic equivalence, a universal compiler rule,
an exact game-function match, or an improvement to the model's weights. `equal`
compares the selected metric, not all bytes. Real-game transfer is explicitly
`untested`. No automatic edits reach the KB, pattern catalog, miner, solver or game.

## Training direction

This is a bounded research and evidence-collection stage. Its persistent notebook
changes future research context. It does **not** yet perform weight updates, RL,
web research, or claim recursive self-improvement.

The next useful model-training cycle is:

1. Reuse the existing synthetic corpus and source-repair trajectory machinery.
   Keep generating-source answers hidden from the solver; use assembly, its current
   candidate, and actual compiler feedback as inputs.
2. Preserve explicit parent/child attempt lineage, branch failures, compiler
   identity, model identity, and total inference/compile cost. A compiler experiment
   from this notebook is not automatically a successful repair training example.
3. Fit a small local LoRA adapter on verified improvements, with preference or RL
   training considered after a supervised baseline works.
4. Evaluate baseline and adapter with the same inference and compile budgets on
   unseen functions and disjoint synthetic families/templates. Keep generated
   near-duplicates together. Never provide evaluation source to the solver.
5. Promote an adapter only after held-out improvement and ratchet checks. Preserve
   the previous adapter for rollback. Repeat only while measured benefit justifies
   the local compute.

This follows the execution-feedback part of coder post-training; it does not
require repeating foundation-model pretraining. See the primary descriptions for
[Qwen3-Coder](https://qwenlm.github.io/blog/qwen3-coder/) and
[DeepSeek-Coder](https://github.com/deepseek-ai/DeepSeek-Coder).
The implementation handoff is `docs/deepseek-local-training-next.md`.

## Adapter training

The panel's **Adapter training** section is a separate, explicit action, not a stage
of the research loop. It starts one bounded source-repair LoRA run and reports what
that run did. It writes no notebook entry, changes no weights outside the adapter
directory it names, and promotes nothing: the evaluation in step 4 above is a
separate, privileged step. **Start training** is independent of **Start research**;
either can run while the other is off.

Limits are set in the panel, and the first one reached stops the run:

| Limit | Default | Hard ceiling |
|---|---|---|
| Maximum training steps | 600 | 20000 |
| Maximum wall-clock minutes | 60 | 480 |
| Maximum training examples | 20000 | 200000 |

The wall clock is enforced by the supervising worker, which also owns the stop, so
it holds even if the trainer ignores its own limits. There is no automatic restart,
no retry after a stop or a failure, and no model download. A restart of the
dashboard reads the same status file and **adopts** the live run: step count,
example count and elapsed time continue from where they were.

**Stop** writes the run's stop file and then terminates the training **process
group** — the worker, the trainer, and every compiler subprocess the trainer
started — with `SIGTERM` and `SIGKILL` if it does not exit. The group spans the
Windows/WSL boundary by running `kill -TERM -<pgid>` through `wsl.exe`, because
killing the Windows-side launcher does not reach processes inside the WSL session.
Stop is idempotent: with no run live it writes nothing and reports the run's
existing state.

Each run lives at `/home/grant/decomp/local-research/training/runs/<run-id>/`
(`events.jsonl`, `trainer.log`, `receipt.json`). The panel polls
`eval/results/local-research/training-status.json`, with `training-receipt.json`,
`training-stop.json` and `training.pid` beside it. The states are:

| State | Meaning | Publishes an adapter |
|---|---|---|
| `off` | no run has been recorded | no |
| `starting` | a worker was launched, nothing published yet | no |
| `running` | the status file is fresh and the run is live | no |
| `stopping` | a stop was requested for this run and the worker is still up | no |
| `stopped` | the stop file fired, or a configured limit cut the run short | **no** |
| `completed` | the trainer exited 0 **and** its receipt was verified | yes |
| `failed` | non-zero exit, or a clean exit with no verifiable receipt | **no** |
| `interrupted` | the worker stopped publishing; no receipt was written | **no** |

A stopped, interrupted or failed run publishes no adapter even when the trainer
left files behind, and `completed` is not a claim about quality: it means the
trainer exited successfully and wrote its receipt. The adapter is named in the
panel only in that state, with "unverified: evaluate before any promotion".

Stage, step count, examples used, elapsed time and the terminal reason all come
from the worker's own advancing status file, which it updates from the trainer's
own stdout — never from process liveness. Liveness only decides whether a
heartbeat has gone stale, which is the `interrupted` state.

The panel launches this command in Ubuntu, from `/mnt/c/Code/gameDecomp`:

```sh
/home/grant/decomp/train-venv/bin/python -m eval.train_repair_sft \
  --dataset /home/grant/decomp/local-research/training/dataset \
  --out /home/grant/decomp/local-research/training/adapters/<run-id> \
  --max-steps 600 --max-seconds 3600 --max-examples 20000 \
  --receipt /home/grant/decomp/local-research/training/runs/<run-id>/receipt.json
```

`--base` and `--split` are appended when the request supplies them. Before starting,
the worker asks the trainer which flags it advertises. A flag listed above that the
trainer does not offer makes the run `failed` with that list in the reason rather
than being passed hopefully; `--max-seconds` is the exception, because the worker
enforces the wall clock itself and records the omission. Removing `--read-only`
enables these controls; in read-only mode the training routes answer 403 and the
panel disables both buttons, exactly as the research controls do.

## CLI and troubleshooting

The dashboard launches this equivalent command in Ubuntu, from
`/mnt/c/Code/gameDecomp`:

```sh
/home/grant/decomp/sbk1/.venv/bin/python -m eval.local_research \
  --state /home/grant/decomp/local-research \
  --repo /home/grant/decomp/sbk1 \
  --view /mnt/c/Code/gameDecomp/eval/results/local-research/status.json \
  --stop /mnt/c/Code/gameDecomp/eval/results/local-research/stop.json \
  --minutes 30 --max-calls 12 --model qwen2.5-coder:14b
```

Builds and research artifacts stay on the Linux filesystem. A missing model or
stopped Ollama produces a visible error without downloading anything. A stale
heartbeat is displayed as interrupted; starting another worker remains protected
by the Linux lock. Dashboard `--read-only` disables research controls too.
`/api/health` checks dashboard availability independently of campaign data.

For a stopped run, its `events.jsonl` contains every started call and compile;
`receipt.json` gives the terminal budget counts. Inspect completed receipts before
drawing conclusions. A restarted dashboard adopts a live worker rather than
resetting its budget.

The training action is served by `/api/training` (status) and
`/api/training/control` (`{"action":"start","options":{...}}` or
`{"action":"stop"}`), and the panel is `eval/progress_training.js`. Both routes
answer 403 in read-only mode. A training run's `events.jsonl` records every stage
change, stop and limit; `trainer.log` is the trainer's own output, line for line;
`receipt.json` is the terminal record the panel's `completed` state requires.
