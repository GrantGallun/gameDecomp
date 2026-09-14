# Residual-driven agent repair

The core loop is deliberately small:

1. Compile one C candidate.
2. Treat the verifier's `exact` boolean as the only success condition.
3. Package the compiler/object residual without calling the weighted score a
   byte percentage.
4. Ask a model for one source-level hypothesis and a bounded replacement.
5. Compile the child, record its parent edge and full proposal receipt, and
   repeat from a small diverse beam.

`solver/modelrepair.py` contains the provider-neutral search kernel.
`solver/residual.py` builds the exactness packet. `eval/agentrepair.py` is the
thin Ollama-backed command-line runner. Existing deterministic passes are not
automatically invoked; a repair should become a permanent pass only after its
mechanism transfers to frozen unseen functions.

The opt-in `--resilient` worker mode (enabled by the completion campaign) now
connects deterministic build metadata recovery and C89 normalization, atomic
header-checked type plans, and target-led differential checks. Behavioral
failures receive semantic-first counterexamples/value trees; byte and semantic
champions are kept separately and reverified on reuse. `--semantic-cases` defaults
to 64 and `--semantic-steps` to 10,000; target-only exploration has a 5,000-probe
budget and retained coverage cases are never dropped to meet the stress floor.
These are finite diagnostic tests, not universal equivalence. Standalone mode
without the flag retains the legacy policy. See `PIPELINE_MAP.md` before adding
another controller, and `eval/experiments/resilient-repair/README.md` for receipts.

## What the model sees

Each step receives the current C, target assembly, compiler error or normalized
instruction diff, deterministic diagnosis when available, and a JSON residual
with:

- the authoritative `compiled` and `exact` verdicts;
- a clearly labelled weighted progress score;
- target/candidate instruction counts and their delta;
- target/candidate `.text` lengths and positional byte distance when objects
  are available;
- structural, layout, relocation, register-allocation, and immediate faults;
- the first changed instruction lines and earlier rejected hypotheses.

Raw `.text` equality is still only diagnostic because relocations can differ
while section bytes are equal. Only `exact=true` terminates successfully.

## Run it

Run from this workbench repository under WSL. Choose an explicit source or an
append-only attempt receipt; the runner never silently chooses a parent by the
weighted score.

```bash
python3 -m eval.agentrepair \
  --repo ~/decomp/sbk1 \
  --db ~/decomp/kb-sbk1.sqlite \
  --function FUNCTION \
  --attempt-id ATTEMPT_ID \
  --out eval/results/agentrepair-FUNCTION.json \
  --cache-dir ~/.cache/game-decomp/agentrepair \
  --draws 1 --depth 4 --beam 3 --max-calls 10 --seed 20260901
```

The root is freshly recompiled, every model call is logged even if malformed or
refused, every valid child is appended to the attempt ledger, and the best
source is written beside the JSON receipt. The global `--max-calls` cap applies
across the whole beam.

The engine accepts any object implementing `ProposalProvider.generate` and a
stable `provider_id`. The default adapter calls local Ollama; a Claude,
ChatGPT, subprocess, or test adapter can use the identical search and receipt
path without changing the kernel. The CLI loads a no-argument adapter with
`--provider package.module:ProviderClass`, so the backend changes without a
solver edit.

## Tool-using search and the paired A/B

`solver/toolagent.py` gives the same local model a bounded observation/action
loop. It can inspect a symbol definition, read a target-repository header,
inspect the focused residual or candidate history, select an earlier candidate,
propose a bounded patch, or finish. It cannot run a shell command or read the
reference C source. The controller owns compilation, writes, rollback, lineage,
and budgets, and requires an inspection before the first patch. JSON assistant
prefill and duplicate-action rejection keep the local adapter on the protocol
without treating malformed responses as repairs.

`eval/toolagent_ab.py` compares that controller with proposal-only repair from
the same freshly compiled parent. Both arms receive the same call seeds and
model/compile caps, and every held-out function is refused. For example:

```bash
python3 -m eval.toolagent_ab \
  --repo ~/decomp/sbk1 \
  --db ~/decomp/kb-sbk1.sqlite \
  --function randomNextObject \
  --attempt-id 19565 \
  --max-calls 6 --seed 20260902 \
  --out eval/results/toolagent-ab-randomNextObject.json
```

The first interpretable DEV smoke activated three inspection actions and one
compiled patch. That patch worsened the true residual, so the controller kept
the root; the proposal-only arm also failed to improve it. This establishes
tool activation and safe regression rejection only, not repair efficacy. The
receipt is `eval/results/toolagent-ab-randomNextObject-smoke-v4.json`.

### Open-book mode

`--open-book` removes the prescribed investigation order and exposes broad
search/read access across target-repository and workbench text, sibling C,
headers, build scripts, solver code, documentation, and prior attempts. It also
permits complete-source replacement. The selected function's reference C
definition is structurally redacted from every file. The remaining boundaries
are experimental integrity and host safety: exact verification, finite compute,
project-only text, controller-owned compilation/writes, and no arbitrary shell.

On `randomNextObject`, the corrected open-book activation run used only 2 of 10
available calls. GPT-OSS made one immediate patch, saw it regress from score
98.75 / byte distance 3 / two register faults to 95.625 / byte distance 6 /
five register faults, then stopped. It never invoked broad search or file read.
That result shows that removing restrictions alone did not induce autonomous
investigation on this parent; it does not test whether retrieved context would
help if a policy actually used it. Receipt:
`eval/results/toolagent-ab-randomNextObject-openbook-v2.json`.

### Curiosity and retrieved-principle panel

`eval/principle_panel.py` freezes a DEV-only panel from fresh true residuals;
`eval/principle_agent_ab.py` runs two causal comparisons. Free open-book versus
curiosity changes only the minimum stopping policy. Curiosity versus principled
keeps that policy fixed and changes only mechanically retrieved guidance from
`patterns/catalog.py` through `solver/principles.py`. Principle comparison runs
only where retrieval activates.

The first valid panel contained eight equal-length/equal-instruction-count
parents with 3-23 differing bytes. Curiosity increased calls from 16 to 47,
tool observations from 4 to 19, and compiled source experiments from 1 to 5.
It reduced `Fwobble` from five to three differing bytes but produced no exact
closures. On the four principle-applicable parents, curiosity and principled
both produced zero exacts and zero byte-distance improvements; principled made
four source experiments versus three. The result supports the activation
mechanism, not repair efficacy. Receipt:
`eval/results/principle-agent-ab-v2.json`.

### Mechanical principle variants

`solver/principle_variants.py` turns the experimental isolated-register-web
guidance into bounded source operators rather than prose. It enumerates C89
declaration order and initializer placement, justified `register` qualifiers,
direct versus pointer-mediated value webs, equivalent increment spellings,
fused preincrement lookups, and explicit scalar-global address lifetimes. The
controller compiles every source and accepts only the verifier's `exact=true`.

The corrected frozen-panel replay activated on three functions whose roots had
3, 4, and 19 differing `.text` bytes. All 49 v2 variants compiled. None became
exact and none reduced positional byte distance; several explicit global
pointer variants regressed substantially. This refutes this bounded rewrite
family on the three-parent cohort, not the broader possibility that a different
source graph can reproduce the register allocation. Receipt:
`eval/results/principle-register-web-v2.json`.

Open-book repository search now builds one raw-text snapshot per project root
and redacts the selected target definition at query time. Tests verify that
successive searches reuse the snapshot while different target names still get
independent redaction. This removes repeated recursive file reads from each
model turn without caching unredacted query output.

## Clean evaluation

`eval/sets/sbk1_v4_clean.json` was assigned from function/TU metadata only. It
excluded every function with an attempt, earlier set membership, or recovered
source. Assignment did not bootstrap a workspace or inspect target source. It
contains 15 DEV and 13 held-out non-leaf functions. No untouched leaf functions
remained after exclusions, and the huge/non-leaf stratum supplied only four of
six requested names, so those shortfalls are explicit in the manifest.

The runner scans every JSON file in `eval/sets/` and refuses interactive repair
of any held-out name. Check the fresh split before an evaluation with:

```bash
python3 -m eval.clean_set audit \
  --db ~/decomp/kb-sbk1.sqlite \
  --project-root . \
  --set eval/sets/sbk1_v4_clean.json
```

The clean held-out split must be run only by a preregistered unattended command.
Do not use its residuals to tune prompts or repair code.
