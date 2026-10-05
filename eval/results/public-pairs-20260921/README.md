# Public repository training corpus — 2026-09-21

Built locally from three pinned public decompilation repositories with IDO 5.3. No paid inference calls, GPU training, solver KB writes, or model promotion occurred.

## Delivered data

| Public repository | Variant | Assembly/C pairs | Train | Dev |
| --- | --- | ---: | ---: | ---: |
| [Diddy Kong Racing](https://github.com/davidsm64/diddy-kong-racing/tree/84f0ea569b07903ba8a4f9f252e8dc8e4ba54bdc) | pal.v80 | 905 | 890 | 15 |
| [Mario Kart 64](https://github.com/n64decomp/mk64/tree/58cfcb022e10f83bc3b889d7e97508cae6837098) | eu.v11 | 557 | 557 | 0 |
| [Super Mario 64](https://github.com/n64decomp/sm64/tree/9921382a68bb0c865e5e45eb594d9c64db59b1af) | eu | 1,353 | 1,261 | 92 |
| **Total** | | **2,815** | **2,708** | **107** |

- [Assembly/source records](data/assembly-source.jsonl): function assembly, source, provenance, split, and build/object references. This is raw paired material, not the repair trainer's task schema. Functions may depend on project types and declarations; do not pretend each row is a standalone C file.
- [Repair tasks](data/repair-tasks.jsonl): **27 artificial repair exercises**, comprising 25 train and 2 dev examples. Each supplies target assembly, damaged self-contained C, instruction feedback, and a separately compiled exact answer. Compatible with `eval.train_source_repair` through its existing task loader.
- [Verification receipt](data/verification.json): counts, dataset SHA256s, exclusions, and scope.
- [Build recipes](data/harvest-recipes.json), [generation report](data/repair-generation-report.json), and [exclusions](data/excluded.jsonl).

The retained verifier artifacts are in WSL at `/home/grant/decomp/public-pairs-20260921-v2`. They include the three isolated source checkouts, both compilations of each retained translation unit, certificates, compiler component hashes, and all 240 repair attempts including failures. `object` paths in assembly/source rows are relative to this WSL artifact root; `context_ref` paths resolve in either this delivery's `data/` directory or the WSL artifact root. Build records also give absolute source paths. Full translation units are verifier context, not model input.

## What verification establishes

Every retained source compilation unit was independently recompiled, with allocated code/data/BSS and relocation expressions checked by `solver.byte_certificate.certify`. Final packaging revalidated 117 objects, source and object hashes, certificate bindings, and each emitted assembly listing against its object.

These are **compiler-derived targets, not locally ROM-certified targets**. Matching a second compilation confirms reproducibility; it does not prove the recipe reproduces the original linked ROM. The recipe variants correspond to the owned dumps, and the DKR recipe uses its previously extracted, hashed asset header. Full ROM builds remain a separate verification stage.

The repair examples use public leaf-function source in a minimal standalone type context. Their targets are compiled in that context, not extracted from a ROM or assumed identical to their original translation units. The mutation changes an integer immediate. All 27 parents compiled and differed; all 27 corrected children passed the object certificate. No answer C was exposed in compiler feedback. These are a narrow curriculum, not recorded successful model repairs or tool-use trajectories.

## Filtering and limitations

The evaluation overlap guard indexed 4,879 functions from both Snowboard Kids evaluation source trees. It rejected 287 normalized exact duplicates and 35 heuristic near duplicates during harvesting. Evaluation source is used only to compute exclusion fingerprints, never as training content.

The harvest then removed 172 normalized duplicates within the public corpus. Finalization removed 40 records compiled from `.inc.c` fragments outside their original translation-unit context, and 146 dev records belonging to files with detected training overlap. Whole source files stay in one partition; repair tasks inherit the original partition. This is a conservative heuristic, not proof that all related functions have been identified. The 107 dev functions and two dev repairs are development checks, not a sealed capability evaluation.

Coverage is partial. Missing generated assets, assembly-backed functions, conditional source bodies, and directories requiring different flags or encoding conversion are excluded or logged as failures. The failure receipts include attempted `.inc.c` fragments, so their counts are not a measure of unsupported standalone game functions. DKR uses `-O2 -mips1`; the selected MK64/SM64 EU game code uses `-O2 -mips2`. Audio/library/Goddard and special encoding paths were excluded from the latter two recipes. F-Zero and Zelda recipes have not been added by this run.

## Existing trainer check

The offline dry run used the installed Qwen2.5-Coder-7B tokenizer, without loading model weights or accessing the network. It accepted all 25 train records structurally. With `--max-seq-len 3072`, **22 fit and 3 are dropped for length**. The longest complete example is 5,865 tokens. Keep the trainer's explicit length filtering; do not truncate away the completion or silently claim 25 examples were trained. No training was started.

From the project root in WSL, repeat the no-training check with:

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  /home/grant/decomp/train-venv/bin/python -m eval.train_source_repair \
  --base /home/grant/decomp/models/qwen2.5-coder-7b \
  --tasks eval/results/public-pairs-20260921/data/repair-tasks.jsonl \
  --out /home/grant/decomp/public-pairs-20260921-v2/dry-run-only \
  --max-seq-len 3072 --max-steps 40 --max-seconds 900 --dry-run
```

Related manifest/extractor tests: **36 passed, 1 skipped** on Windows. The skipped compiler-dependent test requires WSL; the actual corpus run performed the compilation and replay checks above.

## Reproduction

The scripts in this directory reuse the existing allowlist, overlap guard, source parser, compiler wrapper, byte certificate, prompt renderer, and trainer loader. They do not replace the production extraction or training services.

Run `build.py --out <new WSL ext4 directory>`, then `make_repairs.py --corpus <that directory>`, then `finalize.py --corpus <that directory> --delivery <new delivery directory>`. Existing output directories are refused. Prerequisites are the installed compiler/binutils, cached pinned Makefiles under `.tools/corpus`, the evaluation fingerprint sources, and the DKR generated header. The build downloads only the pinned public Git revisions. The corpus collector makes one sequential compilation at a time at reduced process priority. The repair generator caps attempts at 240 and accepted examples at 24 per repository.

The first staging directory without the `-v2` suffix records an unsuccessful attempt to clone a partial local Git cache. The successful run fetched the pinned revisions directly from their public remotes. Neither existing source caches nor earlier datasets were overwritten.

## Next DeepSeek task

> Read this README and `data/verification.json`, then inspect the existing source-repair and tool-action training code before changing it. Use the 27 repair tasks for a bounded integration smoke test; keep their artificial provenance and their inherited splits. Keep paid APIs disabled. Report actual examples retained after tokenization and total local compute.
>
> The valuable expansion is real compiler repair data from the public repositories. Starting with DKR's matching compiler/ISA, use public source ONLY on the label/verifier side. Generate candidates from assembly with the local decompiler, record their real compile diagnostics, and collect verified improvements under a fixed attempt budget. Provide only justified types/declarations to the model; never expose the hidden reference function or its full translation unit. Recompile candidates in the original translation-unit context and compare allocated sections and relocation expressions, not just text similarity. Preserve failures and intermediate regressions without automatically labelling every regression a bad action.
>
> For tool-use training, collect actual observation → tool action → tool result trajectories. A source pair alone is not evidence that an action was useful. Require measured compiler/verifier progress or a useful hypothesis being ruled out before treating a trajectory as positive supervision. Keep tool observations, costs, and remaining budgets in the input; mask loss to assistant actions/completions. Accept alternative C spellings through the same independent verifier.
>
> Freeze any selected training dataset and evaluate on separately sealed Snowboard Kids tasks under equal budgets. Public-corpus dev accuracy and the two dev repair tasks cannot justify promotion. Stop after one bounded experiment and report verified transfer, regressions, failures, and cost before expanding the collection.
