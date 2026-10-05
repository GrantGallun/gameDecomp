# Joint shallow layout reconstruction experiment

The bounded shared-layout treatment produced no improvement over the current
binary-derived draft route on two development caller/callee pairs. Both arms
compiled two of four functions, passed the frontend on those same two functions,
and produced zero exact matches. The compiling baseline and treatment objects
were equivalent in all four paired syntax-form comparisons.

This is a development spike, not a production change or an unseen-game result.
It tests one shared-interface treatment; it does not implement a general joint
source-reconstruction controller or disprove the broader idea.

## Question and selection

Would sharing shallow object layouts through witnessed caller/callee pointer
flows improve source-independent m2c drafts beyond the existing D32 policy?
D32 shares some deeper parameter layouts but leaves these shallow links separate.

A binary-only census used the built target ELF and existing DEV/function/TU
metadata. It excluded 134 held-out names from the repository set manifests,
leaving 460 DEV names. Before observing draft or compiler outcomes it found two
eligible same-TU links, selecting these functions:

- `drawMenuSprite` and `drawMenuSpriteClipped`
- `drawMenuSpriteWithAlpha` and `drawMenuSpriteWithAlphaClipped`

The four selected functions were absent from the research KB's exact-attempt
set at selection. The two pairs are separate connected components in one TU;
their similar names and layouts did not authorize merging their object types.
Binary observations elsewhere in the game remained available to the existing
D32 baseline. No reference C body or reconstructed game header was used to
generate candidates. Symbols, TU identities, public SDK headers and existing
build recipes were supplied context; this is not a raw-ROM-only experiment.

## Comparison

The baseline used current binary-derived function contexts. The treatment added
the two witnessed pointer-flow links and used one common declaration packet
for the group. Both arms retained ordinary and valid-syntax m2c forms, with at
most two draft compiles per function. Optional stride redrafting, local repair,
models and register search were outside this comparison.

| Result | Baseline | Joint context |
|---|---:|---:|
| Generated drafts | 8 | 8 |
| Native IDO compiler attempts | 8 | 8 |
| Compiling and frontend-valid functions | 2 | 2 |
| Exact functions | 0 | 0 |
| IDO errors across rejected drafts | 30 | 30 |

Both wrapper functions compiled in both forms. Both clipped functions failed
in both forms: ordinary drafts had 13 IDO errors each, valid-syntax drafts had
two each. A separate audit rechecked all eight successful object certificates,
all 16 source/target identities and SQLite attempt records. No held-out overlap
was found. Four positive/negative mechanism controls passed. The existing
binary-context/draft/identity test selection passed in WSL (19 tests); the same
Windows selection passed 12 tests and skipped three platform/dependency cases.

## What the result explains

The baseline already declares the clipped callee's inferred struct parameter.
The treatment also types the wrapper's argument, removing a cast at the call.
That source-level change did not change allocated object sections or relocation
expressions. The common context does not by itself supply additional codegen
leverage on these wrappers.

The clipped valid-syntax drafts retain invalid address arithmetic such as
`temp_t4_2 = temp_t4 + 8` with `temp_t4` declared `void *`, and an integer plus
another `void *`. They also mix generated struct pointers with raw-byte indexing.
The compiler failures are observed; the inference that byte-offset-preserving
representation is the next useful lever still needs a separate compiler and
behavioral experiment. Merely making those expressions compile would not prove
their scaling or semantics correct.

Generated body return types also differ from provisional shared prototypes for
the clipped functions. No combined-TU consistency success is claimed. Joint
source reconstruction would need to reconcile those interfaces and layouts,
retain competing hypotheses, and validate all affected consumers together.

## Decision and artifacts

Do not promote this shallow-link policy on the evidence of this spike. Preserve
the no-gain result and investigate address identity, offset scaling and interface
consistency before adding a broad joint search controller.

- [Probe](probe.py): throwaway opt-in census/comparison; never installs types,
  mutates the production KB, or deploys code.
- [Evidence audit](verify.py): run with
  `python eval/results/joint-reconstruction-20260930/verify.py`.
- `preregistration.json`, `census.json`, `selection.json`, `comparison.json`,
  `implementation.json`, `controls.json`, and `verification.json` retain the
  measured boundary and decisions locally.
- `portable/` retains candidate sources, target objects, all native compiler
  receipts and `attempts.sqlite`. All 16 attempts, including eight failures,
  were logged in this private database. Its evidence/inference tiers are empty;
  source/target provenance is retained in the experiment receipts.
- Original native artifacts remain at
  `/home/grant/decomp/experiments/joint-reconstruction-20260930-v1`.

The repository's ignore policy excludes generated data and portable copies from
Git. The report and probe/audit scripts preserve the conclusion and its recovery
paths. Compiler/verifier acceptance, production code, the live campaign and its
knowledge base were unchanged.
