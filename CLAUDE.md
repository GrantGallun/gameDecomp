# gameDecomp — agent context

Automated **matching decompilation**: produce C that compiles byte-identical to an
original game binary, unattended.

**Read [DESIGN.md](DESIGN.md) before writing any code here.** [ROADMAP.md](ROADMAP.md)
has the phase order and the acceptance check that defines "done" for each one.
[TRAINING.md](TRAINING.md) covers the model-training path and, importantly, what the
`attempts` table must log from day one for that path to stay open.

## The one-line thesis

> The model proposes, the database remembers why, and the compiler decides.

This is a knowledge base with a solver attached, not a model. The LLM is a hypothesis
generator with no authority. Byte-exact object comparison is the only source of truth.

## Invariants — do not violate these

1. **Build always green.** Functions are `asm` (correct by construction) or `matched`
   (verified byte-exact). Never commit "probably right" C to the real build path;
   non-matching work lives behind `#ifdef NON_MATCHING`.
2. **The ratchet.** Global match count never decreases. Every KB mutation runs inside
   a savepoint that rolls back if it does.
3. **Evidence vs inference.** The `evidence` tier is mechanically derived from the
   binary and is immutable — code that writes evidence from a model output is a bug.
   Only the `inference` tier is writable and retractable.
4. **Every inference cites evidence.** Reject uncited claims at the API boundary.
5. **Unknown is the default.** Emit `char unk_00[0x24];` rather than a guessed field.
   A model will happily invent plausible field names; that is the failure mode this
   whole design exists to prevent.

## How to not ship analysis bugs

Every analysis bug so far had one shape: **a hypothesis about compiler behaviour
encoded as a rule, without testing it.** The instruction observations were always
correct; the interpretations were not. `sw` really does write four bytes at 0x24 — it
just does not follow that the field there is four bytes wide.

Four rules, in order of how much they catch:

1. **The finished decomp is a test oracle.** SBK1 is 100% matched, so its headers are
   the answer. Correct code cannot contradict itself: any finding your pass produces
   on SBK1 is either explainable in the source or a bug in your pass. Run
   `python3 -m eval.validate_evidence --repo ~/decomp/sbk1 --db <db>` after touching
   anything in `miner/` or `kb/`. It exits non-zero on unexplained disagreement.
2. **Explained-or-broken.** Never tolerate a finding silently. Trace it to source and
   then either fix the code, model the pattern, or whitelist it *with a written
   reason*. The test asserts the known set, so anything new is a regression. This is
   the ratchet applied to our own analysis quality.
3. **Probe, don't assume.** `tools/probe_rabbitizer.py` derives the width/signedness
   mapping empirically instead of hardcoding a guess. Extend that discipline to
   semantic assumptions, which is exactly where the bugs were.
4. **Police the evidence/inference boundary.** Anything converting observations into
   conclusions is inference, even when it lives in a file called "the sweep". It needs
   citation and retraction discipline like any other claim.

Ground truth is for **checking** the miner, never for feeding it. Letting
`eval/ground_truth.py` output flow into the KB would be teaching to the test and would
invalidate every number after it.

### Bugs this caught (all in the analysis, none in the evidence)

- **Bulk copies.** IDO compiles struct assignment into runs of word loads/stores at
  consecutive offsets, which say nothing about field widths.
- **Signedness casts.** `lh` and `lhu` on one location is ordinary C, not a conflict.
- **Unions.** An offset is legitimately polymorphic, so acceptable widths are a *set*.
- **Brace depth.** Matching the first `};` ended structs at their first anonymous
  union, silently discarding half the ground truth — accuracy looked fine while
  coverage was quietly halved. Coverage numbers deserve the same scrutiny as accuracy.
- **Padding in unions.** `pad18[4]` is a real one-byte field when another arm declares
  an s16 there. Offsets described *only* by padding are unfalsifiable and must be
  reported as uncheckable, never counted as agreement.

## Patterns are the product

Where there is a pattern there is a function, and where there is a function there is
use. Every recurring shape found in compiled output goes in `patterns/catalog.py` with
provenance — not as an ad-hoc fix buried in whichever module first tripped over it.

Each entry is actionable in one of three ways: **evidence** (changes how observations
are interpreted), **solver** (tells the refine loop what C shape produces this asm), or
**review** (a signal to weigh, not a rule). `confirmed_on` empty means hypothesis, and
**a hypothesis does not get to change behaviour** — that is the exact failure mode this
project keeps catching in itself.

Two ways patterns arrive, and use both:

- **Reactively** — you trip over one debugging. Catalog it instead of patching in place.
- **Proactively** — `python3 -m patterns.mine --repo ~/decomp/sbk1` mines frequent
  opcode n-grams from the matched corpus. 2,113 matched functions are 2,113 confirmed
  (C, asm) pairs; the idioms a human learns over months are frequent shapes with stable
  C counterparts, and they are findable by counting. Confirm operands against source
  before cataloguing — frequency is not correctness.

Mining earns its keep beyond idioms: it surfaced 629 data symbols being disassembled as
code (`mfhi mfhi mfhi mfhi` is not a real instruction sequence), which the golden test
had missed because fabricated rows land in *uncheckable* buckets rather than failing a
check. Two different lenses catch two different bug classes.

## Evaluation hygiene

The first target is a game already decompiled to ~100%, so ground truth exists. That
makes contamination the primary risk:

- **Never** put held-out reference source into a prompt, including as a few-shot example.
- Held-out sets are defined in `eval/` and are the only basis for reported numbers.
- Baseline to beat is ~1.2% byte-exact (published single-shot SFT result). Report
  honestly against it.
- Prefer obscure targets over famous ones. SM64 and OoT source is certainly in
  pretraining data; memorization would make held-out numbers fiction. See the
  contamination check in [TRAINING.md](TRAINING.md).

## Environment

Build inside **WSL2 Ubuntu**, not Windows. The N64 toolchain is Linux-native and IDO
runs via `ido-static-recomp` — no Docker needed.

Keep the build tree on the **WSL filesystem** (`~/`), not `/mnt/c`. Cross-filesystem
I/O is slow enough to break the Oracle's latency target, and the Oracle runs millions
of times. Design docs can live on the Windows side; the build tree cannot.

## Conventions

- Python; SQLite for the KB; Docker for period compilers.
- The Oracle (`oracle/`) must stay fast — it runs millions of times. Cache on
  (source hash, flags hash).
- Log every attempt to the `attempts` table, including failures. It is the debugging
  record now and the training set later.
- Miner passes are deterministic and LLM-free. Keep it that way.

## Status

**Do not hand-edit this section.** Run `python3 -m eval.status` and paste. It said
"Design complete. No implementation yet." for weeks while 65 tests passed and 33
functions matched — an external review caught it. Any agent reading a stale status
reasons from a false premise, so a generated number is worth more than a careful
sentence.

Target is SBK1 (N64, IDO 5.3 `-O2`). Last generated 2026-08-28:

| | |
|---|---|
| functions byte-exact | **34** of 91 attempted |
| attempts logged | 1,939 |
| evidence rows | 72,845 |
| **inference rows** | **0** |
| tests | 65 |

**The inference tier being empty is the headline, not the match count.** The thesis is
"the model proposes, the database remembers why", and the database has never remembered
anything. Every function is reconstructed from an address and a width, with no
accumulated type knowledge — which is why matching collapses at exactly the tier where
types start to matter (tiny/small 100%, medium 10%, large 6%).

Known-dead directions, so they are not retried: prompt enrichment (7 nulls), telling the
model a type exists without binding it to a symbol, whole-function context/length
management (region splitting, compression, sequential composition — all null, and
removing 100% of refusals produced 0 extra matches). See `patterns/hypotheses.py`.

Next action: bind real prototypes and types to symbols (`miner/import_existing.py`,
still unwritten) so the inference tier stops being empty.
