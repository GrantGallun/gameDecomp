# gameDecomp — agent context

Automated **matching decompilation**: produce C that compiles byte-identical to an
original game binary, unattended.

Read [PIPELINE_MAP.md](PIPELINE_MAP.md) before adding pipeline machinery. It maps
existing mechanisms to their actual controllers, tests, receipts, and limits.
Update it when changing wiring; do not confuse an available module with an
enabled workflow or reimplement a mechanism already listed there.

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

### The silent decline

A fifth rule, and it caught four bugs in one sitting: **a pass that returns nothing
looks exactly like a pass with nothing to do.** Every one of these declined on the
precise residual it was written for, and all of them looked healthy from outside:

- `constraints()` dropped a whole base register when ONE of its offsets was
  ambiguous — discarding twelve unanimous constraints on `v0` because a thirteenth
  disagreed. `v0` is the return-value register; base register is not object identity.
- It then merged bases into one map that had to be globally consistent, so `a2`'s
  eighteen clean constraints were thrown out because an unrelated pointer `t6` also
  had an opinion about offset 0.
- `_fields()` skipped a member whose type was not a primitive **without advancing the
  cursor**, so one `Vector3 position` silently moved every later field. The parser
  reported offsets ending at 68 for a struct whose compiled accesses reach 124.
- `reloc_padding_rewrites` searched only for `struct {...} sym[8];` and declined on
  `typedef struct {...} T; extern T sym[4];`, which is how headers are actually written.

None of these raised anything. The sweep reported "0 proposals" and moved on, and the
fault totals looked like a hard problem rather than a parser bug.

So: **every generator needs a test that asserts it FIRES on its motivating residual**,
not only tests that it declines on the wrong ones. Declining is the easy half to get
right and the easy half to test, which is exactly why the failures all landed on the
other side. When a pass produces nothing on a residual of the kind it owns, that is a
finding to explain, not a null to accept.

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

Target is SBK1 (N64, IDO 5.3 `-O2`). Last generated 2026-09-24:

| | |
|---|---|
| functions byte-exact | **393** of 1074 attempted |
| — of which SOLVED | **278** |
| — of which header-assisted (reconstructed include/game) | 34 |
| — of which reference-type-assisted (a type only the target's src/ defines) | 26 |
| — of which recovered from target source | 55 |
| attempts logged | 95,904 |
| evidence rows | 72,845 |
| **inference rows** | **0** |
| tests | 4,118 collected (`eval.status` prints 0: its WSL collection fails; `tests/test_campaign_service.py` fails to collect) |

Read the four sub-rows before quoting the headline. `recovered` is the reference
decomp's own answers copied in and oracle-verified — legitimate for seeding the
sibling pool, fatal to a number quoted as capability. `header-assisted` is a third
tier added 2026-09-01: a reconstructed `include/game/**` header supplies the decomp
team's prototype *and* struct layout, so it is neither a copied body nor something
the pipeline could reach from binary evidence. `reference-type-assisted` is a fourth,
added 2026-09-21: the winning source uses a type that only the target's own `src/*.c`
defines — no header, SDK or reconstructed. **SOLVED is the only capability number, and
it is a lower bound on assistance, not a clean count** — see the next paragraph.

**The drafts the pipeline starts from are contaminated (2026-09-21).** `solver/workspace.m2c_draft`
prefers an existing `nonmatchings/<fn>/base.c`, and that directory belongs to the target repo: only
3 of 2,125 drafts carry this project's own assembly-only receipt. On the 200-state intake frame, 98
drafts use a type or field name found only in the target's `src/*.c`, and re-measuring the same
functions from assembly-only drafts moved IDO-compiling 53 → **16** and byte-exact 3 → **2**. Set
`GAMEDECOMP_ASSEMBLY_ONLY_DRAFTS=1` for any number meant as capability. Full account:
`eval/results/intake-20260921/CONTAMINATION.md`.

**The empty inference tier is no longer the headline — it was tested and the premise
failed.** A paired A/B on hard_v1 dev gave one arm a KB deliberately loaded with the
reference decomp's own types and left the other clean. Exact was 0 in both arms, and the
typed functions got *worse*: mean best 26.5 → 11.2 against 35.3 → 31.5 for untyped
controls, a difference-in-differences of −11.5. Perfect type knowledge, handed over for
free, harmed generation. Do not build a broad type-inference tier on the old reasoning;
diagnose why declared-type context hurts before spending anything else on it.

**What does work is deterministic repair driven by the oracle's own diff.** The KB knows
which offsets the binary touches but nothing maps a *declaration* to an offset; the diff
states it outright (`-lbu v1,0x24(a0)` / `+lbu v1,0(a0)` means the field you put at 0
belongs at 0x24). `solver/diffrepair.py` reads offset, width and ordering constraints
from that and repairs the struct with no model involved. It produced the only new match
since the corpus was built.

Known-dead directions, so they are not retried: prompt enrichment (7 nulls); oracle-grade
types (above); layout repair driven by the KB rather than the diff (three interventions,
0 matches); whole-function sibling mirroring at the current pool size (0% in-pool
coverage — it is a flywheel, revisit as the matched set grows); the permuter on
structural residuals; whole-function context management. See `patterns/hypotheses.py`
and `memory/hypothesis-graveyard.md`.

**Rank work by tractability, not score** (`python3 -m eval.triage`). A function at 70%
whose residual is two offset faults is closer to done than one at 96% whose residual is
thirty branch faults. The match above was the smallest tractable residual, not the
highest-scoring candidate — and `bootThreadMain` sits one instruction from exact and is
unreachable, because that instruction is compiler padding rather than code.

Next action: the structural wall. 0 matches of 41 in medium/large/huge, and the residuals
there are branch shape, missing instructions and jump tables — see SAILR (USENIX Sec '24)
on inverting compiler goto-inducing transformations, and consider cataloguing IDO switch
shapes in `patterns/catalog.py`.
