# Model-authored repair tools

This is an opt-in laboratory implementation of the user's coverage-first loop:
observe recurring failures, author and revise a reusable tool on development
cases, freeze it, compare with the ordinary rewrite generator, retain only a
verified improvement, and export audited development decisions.

## Run

From this repository in WSL Ubuntu:

```sh
/home/grant/decomp/sbk1/.venv/bin/python -m eval.tool_learning_pilot \
  --out /home/grant/decomp/experiments/tool-learning-new-run \
  --model qwen2.5-coder:14b
```

Requires an installed local Ollama model, Linux Bubblewrap with namespace support,
libseccomp, MIPS objdump, and the installed IDO recompiler. `--endpoint auto` checks
loopback and WSL's Windows gateway. There is no model download or remote endpoint.
Use a new output directory each time. Compilation artifacts must live on the WSL
filesystem. The Python API `run_experiment(cases, propose, oracle, output, limits=...)`
also accepts operator-curated panels; each case supplies `id`, `family`, `split`
(`dev` or `eval`), candidate `source`, and a trusted target-object path.

## Execution and acceptance

Only development candidates and observed residuals reach the authoring model.
It returns `name`, `rationale`, and a Python program that reads one JSON observation
and emits up to eight C candidates. The sandbox supplies no host repository, home,
network, target source or verdict API. Kernel restrictions prevent additional
processes, exec, anonymous memory files and new namespaces. Root, runtime and
device mounts are read-only; scratch filesystems, address space, CPU, output and
wall time are bounded. No unsafe execution fallback exists.

The separate compiler broker accepts a small plain-C domain: no preprocessing
directives, strings, comments or assembly. It runs IDO `-O2 -mips1 -G0 -non_shared`
with a fixed scalar prelude. This is an explicit synthetic recipe, not a claim to
implement arbitrary game translation-unit build contexts. The generated program
never receives the IDO compiler mount or mutable verifier state, and cannot exec
other runtime binaries. Exact results require the
existing section/relocation certificate and an independent rebuild. These are
object certificates, not whole-ROM certificates.

The baseline uses `solver.rewrites.propose` for bounded one-step proposals. The
tool arm tries its proposals and then the same baseline. Both compile the root,
use fresh compiler scratch and share per-case compile/time ceilings. Confirmations
consume compiler budget. Tool failures are logged and fall back; compiler failures
remain unknown. Every planned case stays in the denominator. Shared target,
compiler or experiment identity changes quarantine results and disable export.

Private retention needs a motivating development repair, no lost covered case,
complete observations, and either added coverage or equal coverage at lower
total measured cost. Authoring, development and failed work are charged against
the tool arm's total compiler/time allowance. Token use is reported separately.
This first baseline is not the complete production search policy, and these
measurements cannot establish an advantage over the full campaign.

## Artifacts

- `preregistration.json`, `panel.json`, `target-construction.json`: frozen synthetic
  test setup and target compilation receipts.
- `author-N.json`: actual local-model response and measured usage, including
  malformed responses.
- `experiment/manifest.json`, `proposal-N.json`, `frozen-tool.json`: pinned inputs
  and model-authored artifacts.
- `experiment/events.jsonl`: durable attempt starts, source-bound results,
  sandbox failures, authoring feedback, freezes and acceptance decisions.
- `experiment/report.json`: complete coverage, costs, failures and quarantine.
- `experiment/development-sft.jsonl`: successful development tool-use decisions,
  with pre-action prompts and confirmation receipt IDs. Compatible with the
  existing action-SFT record format; not automatically admitted to training.
- `experiment/library/tool.json`: created only when the retention gate passes.
  It does not register the tool with the production solver.

No weights are updated. The initial data path teaches tool use when an available
tool has demonstrated a repair. It does not yet label negative choices, learn a
tool-authoring policy, or measure a trained model's decision quality.

## Smoke evidence

The first two runs used installed `qwen2.5-coder:14b`. The first proposal failed
the tool protocol. After development-only feedback was implemented, three model
attempts still did not repair the motivating examples. Both runs retained 3/12
synthetic evaluation cases with zero losses and no missing cases; no tool was
retained. The second run recorded 38 scored compiler calls and 5,903 model tokens,
plus the separately recorded 14 target-construction compiles.

A third exposed smoke used installed `qwen3:14b` with the hardened sandbox and
accounting fixes. It also covered 3/12, lost zero cases, skipped none, and failed
the motivating development gate: 38 scored compiler calls and 5,710 model tokens,
plus 14 target-construction compiles. Both models confused the input protocol or
proposed ineffective C transformations. No model-authored tool was retained, and
these runs produced zero verified development SFT examples.

The final verification ran 117 tests across the new loop, existing coverage,
byte certificates and rewrite generator. A developer-authored positive control
passed the entire real-IDO loop: three evaluation objects certified and two
development examples exported. This control verifies the machinery, not model
learning. An independent review's resource containment, deadline, source identity,
quarantine and malformed-output accounting findings were fixed and regression
tested. Receipts: `eval/results/tool-learning-20261003/`.

These templates have been repeatedly inspected and run: results are exposed
integration smoke, not fresh held-out transfer. A real coverage claim next needs
a separately frozen game panel, its actual per-TU compiler context and existing
certificate/ROM gates, provenance audit, and the complete campaign baseline.
