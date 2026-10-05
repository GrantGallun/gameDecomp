# Provenance of the narrow-RSI receipts: MIXED — marked historical, not rewritten

Written 2026-09-21 for Phase A item 4 of `docs/deepseek-next-experiment-20260921.md`. **No original file
in this tree was modified or deleted**; everything below is read from the receipts as they stand.

## Verdict

**The receipts in this directory are mixed.** Four run directories are present, and they do not partition
into four runs. The clearest symptoms:

* one run's stage receipts, generations and notes landed in the ROOT directory while **its own event log,
  ledger and summary receipt landed in `superseded-r3/`**;
* `superseded-r3/` therefore contains **two different cycles**;
* `REPORT.md` §2 quotes numbers from **three** of them at once.

## What each directory actually contains

| directory | receipt says | its own stage receipts describe | S0 fingerprint | research receipt | ledger (own file) |
|---|---|---|---|---|---|
| `./` (ROOT) | `rsi-smoke-3`, stage `done`, evidence `unknown` | one cycle 20:29:23 → 20:37:38: frozen → research (calls 6, compiles 8, confirmed 1, finding `rsi-smoke-2-research-004`) → S1 frozen (`cbf16f453ff79b13`) → evaluate (notes_enabled 1, retrieval 24/30) → decide `ineligible` | `251fd31ec0f172a9` | notebook = **absolute** root path | **evaluate only**: 6 + 18 = 24 compiles, **0 model calls** |
| `superseded-r1/` | `rsi-smoke-1`, `refuted` | one complete cycle 20:21:29 → 20:22:58, research calls 5 / compiles 4, cluster `50477700`, **no S1** | `687796e7b3b8a31c` | notebook = **relative** path | research (5 calls, 4 compiles) + evaluate = 10 compiles |
| `superseded-r2/` | `rsi-smoke-2`, `refuted` | one complete cycle 20:25:10 → 20:26:26, research calls 5 / compiles 4, cluster `b7bd868662d0`, **no S1** | `687796e7b3b8a31c` | notebook = **relative** path | research (5 calls, 4 compiles) + evaluate = 10 compiles |
| `superseded-r3/` | `rsi-smoke-2`, `confirmed`, `research_used {calls 6, compiles 8}`, spent 32 compiles / 6 calls | **two cycles**: (a) its own 20:27:01 → 20:28:41, research calls 4 / compiles 2, **confirmed 0, no S1**, S0 `ed9928441d7c4cde`; (b) an event log / ledger / receipt for the ROOT cycle 20:29:24 → 20:31:15 (research calls 6 / compiles 8, confirmed 1) | `ed9928441d7c4cde` | notebook = **relative** path | research (6 calls, 8 compiles) + evaluate (6 + 18) = 32 compiles |

## The evidence, named

1. **The root run's stage events are in `superseded-r3/`.** `events.jsonl` (ROOT) starts at 20:35:55 with
   `stage_skipped_resumed` ×3 and one `failed` event — it contains **no** frozen, research or verify event,
   although `stages/{frozen,research,verify}.json` exist and are timestamped 20:29:23–20:29:48.
   `superseded-r3/events.jsonl` carries exactly those events: `stage_frozen_done` 20:29:24,
   `stage_research_done` 20:29:48 with `calls=6 compiles=8`, `stage_verify_done` 20:29:48 `confirmed=1`,
   `stage_assemble_done` 20:29:48 `notes=1`, then evaluate 20:31:13 and decide 20:31:15.
2. **The research work is charged once, in the wrong ledger.** `superseded-r3/budget.jsonl` contains
   `spend:research {model_calls: 6, compiles: 8}`; `budget.jsonl` (ROOT) contains no research event at all
   (4 events: evaluate reserve, evaluate-setup reserve/spend, evaluate spend 18). `REPORT.md` §2's budget
   row — "spent 6/12 model calls, 32/72 compiles" — is `superseded-r3`'s ledger total, not the ROOT's.
3. **A note sourced from the ROOT run sits in `superseded-r3/`.** `superseded-r3/notes.jsonl` holds
   `note-rsi-smoke-2-research-004` (`status: proposed`) whose `provenance.notebook_row_sha256` is
   `6097e0d000e6e173e8ea220877aa989feef016ca2dee7178d397194b1e00de5a`, which is exactly
   `sha256(json.dumps(<ROOT stages/verify.json>.finding, sort_keys=True))`. `superseded-r3/stages/verify.json`
   itself says `rows=1, confirmed=0, finding=None`, so that note **cannot** come from r3's own notebook.
   It was written at 20:29:48, after r3's own cycle had finished at 20:28:41, and it carries none of the
   `provenance.confirmation` block the current `intervene_from_finding`/`confirm_applicability` path writes.
4. **Three S0 identities for what the report calls one generation.** `687796e7b3b8a31c` (r1 **and** r2),
   `ed9928441d7c4cde` (r3), `251fd31ec0f172a9` (ROOT). r2 → r3 differ **only** in the evaluator entry
   (`12a5eb17…`/9979 bytes → `d747ff9d…`/10593 bytes): `eval/rsi_transfer.py` itself changed between the two
   freezes. r3 → ROOT differ **only** in `memory.path` being relative vs absolute — one path spelling, two
   generation identities. `REPORT.md` §2 cites `687796e7b3b8a31c` for "frozen", which is r1/r2's S0 and
   **not** the ROOT S0 (`251fd31ec0f172a9`) that its own S1 records as `parent`.
5. **The superseded research receipts record a relative notebook path.** All three name
   `eval/results/narrow-rsi-20260921/research/state/notebook.jsonl`. Read from the repository root that is
   the **ROOT run's** notebook (today: 2 rows, 1 confirmed) — not `superseded-rN/research/state/notebook.jsonl`,
   which is where their own research actually wrote. The receipts therefore do not determine which rows
   their `verify` stages read.
6. **The run id and the artifacts disagree.** The ROOT receipt/`state.json`/`config.json` say
   `experiment_id = rsi-smoke-3`, but the research rows and run directory it references are
   `rsi-smoke-2-research-*`. `config.json` in the ROOT was rewritten at 20:35:43 — after the
   frozen/research/verify stages (20:29:23–20:29:48) and before assemble (20:36:17).
7. **Read-only re-verification with the current `eval.generation_manifest.verify()`** (run from Windows,
   2026-09-21): all five manifests report `manifest.mode = not-recorded` (they predate the freeze-time
   manifest digest) and `missing artifact` problems — 7 for the ROOT's S0 and S1, 6 plus one
   `artifact appeared since freeze` for each of r1/r2/r3's S0 — because the recorded base-model/adapter
   paths are WSL paths (`/home/grant/...`, `/mnt/c/...`) that do not exist on the Windows side. That is an
   environment fact, **not** evidence that any artifact changed. The one `artifact appeared since freeze`
   on r1/r2/r3 is real: their `memory.path` is the **relative** notes path, which today resolves to the
   ROOT run's `notes.jsonl` — the same defect as item 5, seen from the other side.

## What is NOT verifiable about these receipts

* **How many invocations wrote them, and which.** They carry no invocation id, and every file here predates
  `Experiment.config_sha256`, so no receipt can be bound to the configuration that produced it.
* **Whether the root cycle's research work was charged more than once.** It is charged in
  `superseded-r3/budget.jsonl` and not in the ROOT's; whether any other ledger (including a re-run whose
  files were later moved) also charged it cannot be determined from this tree.
* **Which S0 the ROOT's S1 descends from**, beyond what S1 records. It was written by loading
  `generations/S0.json` in the ROOT (`251fd31ec0f172a9`), but `REPORT.md` cites `687796e7b3b8a31c`.
* **Whether the named artifacts still have the bytes that were frozen.** Their content can only be checked
  from WSL, and none of these manifests carries a freeze-time digest of its own bytes, so their inline
  components (`prompts.system`, `tool_schema.action_space`, `budgets`) are unverifiable-by-content from the
  files themselves — the report for them is `unverified`, never `verified`.
* **Which notebook each superseded research stage actually read**, because the recorded path is relative
  and now points into another run.
* **Whether r3's note was ever enabled.** It is `proposed`; no confirmed note exists in r3's tree. The one
  confirmed note in this experiment is the ROOT's `notes.jsonl`, which the ROOT's own `candidate-frozen`
  stage turned into S1 `cbf16f453ff79b13`.

## What may be cited, and how

* **Self-consistent as a single cycle:** `superseded-r1/` and `superseded-r2/` (each: one frozen S0, one
  research stage, refuted, no child) — with the relative-notebook caveat of item 5.
* **The S0 → S1 cycle:** the ROOT's `stages/*.json`, `generations/S0.json`, `generations/S1.json` and
  `notes.jsonl` (20:29:23–20:37:38). Its own ledger does **not** fund the research it records, and its
  `receipt.json` says `evidence_status: unknown` even though its `verify` stage recorded a confirmed
  finding — so cite the stage receipts, not the summary receipt, for that cycle.
* **Not a single run's record:** `superseded-r3/` as a whole. Its `budget.jsonl`, `events.jsonl`,
  `receipt.json`, `state.json`, `rsi_state.json` describe the ROOT cycle; its `stages/*.json`,
  `generations/S0.json` and `notes.jsonl` belong to two different ones.
* **Superseded for budget and provenance claims:** `REPORT.md` §2's "frozen … fingerprint
  `687796e7b3b8a31c`" and its budget row, per items 2 and 4.

The RSI result itself — no measurable transfer from the confirmed note, gate `ineligible` — is unaffected:
it rests on the ROOT's `stages/evaluate.json` (both arms, 6 functions, 30 decisions each,
`notes_enabled: 1`, retrieval fired on 24 of 30) and `stages/decide.json`, which are internally consistent
and are the receipts the report's §3 conclusion should be read from.
