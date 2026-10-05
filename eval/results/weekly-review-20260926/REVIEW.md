# Weekly Claude Code review: September 21–26, 2026

**Decision: finish delivering the proven mechanisms into the campaign, then pursue targeted compiler-inverse research.** The immediate blocker is implementation and operation. The frontier beyond that is a real research problem: choosing C edits that reliably change register allocation, temporary materialization and control-flow shape. There is no evidence here of a fundamental impossibility for ordinary C functions.

This review uses the three substantive project conversations from this week, their experiment receipts, current code, a fresh `python -m eval.status`, read-only database queries and a dry evaluation of the frozen scheduler. Historical experiment results below were inspected, not recompiled during this review. No production code, campaign state, pause setting or ledger was changed.

## What is true now

Fresh audit at 2026-09-26 17:18 UTC: [live-audit.json](live-audit.json).

- The native WSL checkpoint is **28385**. Its campaign has **972 object-exact/integrated nodes out of 2,051**, including 21 integrated, plus 22 function-exact pending integration. A durable pause marker exists and no campaign worker or supervisor process is running.
- The campaign's own yield ledger reports **276 distinct exact functions gained, zero lost**. The 972 headline includes inherited exact state and must not be presented as 972 autonomous discoveries.
- Fresh research-KB status: **858 byte-exact; 718 labeled SOLVED; 61 header-assisted; 24 reference-type-assisted; 55 recovered**. These tiers have known lineage/header-detection gaps. “718 SOLVED” is not a clean capability count.
- The campaign attempts DB has 952 distinct exact functions. The raw union with research-KB exact attempts is 1,139, but that union is **not audited for frontend acceptance or provenance**. It is not a replacement project headline.
- The Windows-side campaign pointer and service/health files are stale. The native service file retains a September 19 permission error against the former Windows checkpoint path; a September 20 amendment relocated the state afterward. That stale error is not proof the same fault is active today. The pause and absent processes are directly observed.

The frozen scheduler was evaluated read-only against the full native checkpoint: [scheduler-audit.json](scheduler-audit.json). It supplies a next profile for **633 functions**, including 139 `regalloc_search`, 116 `structural_rewrites`, 11 `stack_layout`, and 13 certificate-digest re-certifications. There are **381 unresolved, non-parked functions without a next profile**, plus 43 parked functions. No scheduler evaluation errors occurred.

Consequently, the older recommendation to fork the entire campaign simply to make the new certificate run is superseded by the restored `recertify@<digest>` machinery. Some exhausted mutation profiles still need deliberate re-eligibility when their implementation changes; restarting alone does not prove coverage of those functions.

## What the week's evidence establishes

| Finding | Evidence and implication |
|---|---|
| Working capability was lost during deployment | The September 19 main-to-frozen replacement removed profiles implemented only in the frozen tree. The September 25 restore recovered them and ported their wiring to main. The lost `regalloc_search` profile accounted for 184 historical campaign gains; stack layout accounted for 18. This was an implementation regression, not a compiler-theory limit. See [PIPELINE_MAP](../../../PIPELINE_MAP.md). |
| Binary-derived types are the strongest demonstrated clean drafting advance | The route recorded **441 gate-passing matches**, of which **410 were already campaign-exact and 31 were new to the project**. Reproducing those 410 without reference assistance is valuable capability evidence. It is not 410 new coverage gains. The route remains experiment-only: no production drafting, intake or campaign caller uses it. See [binary-types result](../binary-types-capability-20260924/RESULT.md). |
| Existing repairs can fail silently when their inputs are absent | `evidence_site` required source attribution that a search harness never computed. Running the full scorer and repair stream closed 20 of 130 structurally correct campaign functions; re-certification, the next structural band and one clean search result brought the recorded total to **33 project-new functions**. See [operand repair](../operand-repair-20260925/RESULT.md). |
| Some genuine repair gaps remain | On the restored 22-function panel, the register search closed **0 of 15 register-choice cases**, despite spending its budget or exhausting proposals. Two new local-variable families then raised exacts from **3 to 6 on 194 fixed starts** with zero losses. These prove additional mechanism coverage; several wins were already in the campaign ledger. The two families remain absent from the frozen mutation module. See [restored holes](../restored-holes-20260925/README.md). |
| The big-function wall survives delivery fixes | The campaign yield ledger records **0 new exact functions at least 1 KiB** across 1,110 work items. The campaign frontier study found a median of **40 structural instruction differences in large functions**, versus 2 in small functions. Register changes alone will not close that population. See [frontier study](../frontier-20260924/RESULT.md). |

Three corrections matter when interpreting earlier explanations:

1. **An apparent offset fault is not necessarily a layout fault.** Misaligned instructions and stack slots inflated the “wrong struct offset” demand. Use attribution and compiler-stage diagnosis before choosing an owner.
2. **A register difference is not necessarily allocator failure.** In the diagnosed 130-function pool, 50 had correct allocator colouring; 40 of those still had operand faults. Genuine selection and splitting cases were separate groups. See [allocation census](../alloc-census-20260924/RESULT.md).
3. **Knowing the forward rule is not an effective inverse.** The allocator inverter's third version placed the targeted range correctly in 18 of 85 measured edits but produced zero exacts. The missing result is a C transformation that causes the desired decision while preserving the other decisions. Calling the whole compiler “understood” overstates this evidence.

## Recommended next sequence

### 1. Complete one delivery milestone

Make the proven route run from the normal controller, with its own attempts and verification receipts:

- Validate the restored frozen campaign on a bounded batch using native WSL state. Start with deterministic profiles and the certificate cases; inspect real dispatch, proposed candidates, compiler scoring and retained results.
- Deliver `pure_inline` and `operand_local` through the scoped frozen-amendment path. Main is now the source of the restored wiring; preserve that direction.
- Promote the experimental binary identity/context generator into production drafting. Regenerate and cache facts from the target binary, preserve unknowns and provenance, and use the public SDK prelude. Feed the resulting drafts through the ordinary frontend, scorer and repair stream. Keep each existing best candidate unless the normal acceptance rule selects a better result.
- Supply the complete attribution/context needed by evidence-driven owners. `rodata_symbol` is present as a module but still needs a real caller carrying the target object and ELF.
- Audit eligibility after delivery. Reopen only the appropriate changed strategy where needed, rather than resetting every model budget.

**Acceptance:** a small predetermined set reproduces known binary-type, local-variable and certificate wins through normal campaign execution, without importing winning C; every attempt is logged; all prior exacts remain; fresh gains are deduplicated against both ledgers; clean and assisted routes are reported separately. Only then widen the run. Relevant wiring failures must be explained rather than dismissed as “pre-existing.”

This is the best immediate task because it turns already demonstrated capability into unattended behavior. It also creates a trustworthy baseline for deciding which remaining problems actually require research.

### 2. Research one compiler cause at a time on the remaining frontier

Re-census after the delivered mechanisms have run. Start with a fixed small/medium panel having zero or very few structural differences. Separate operand/stack faults, register selection, range splitting, compiler temporaries and assembler scheduling.

For a representative case, identify the first divergent compiler decision, build a small synthetic experiment that reproduces it, predict one C edit's effect, and compile it. Measure **whether the intended action happened**, what collateral differences it caused, and whether it transfers to untouched functions. A mechanism earns integration when it fires on its motivating residual and improves a paired panel without losing existing exacts.

Selection/splitting is the highest-value research question supported by the near-match evidence. For larger functions, the next experiment should separately measure structural progress and register fallout so a useful structural change is not judged solely by a single similarity score. That remains an experiment, not a demonstrated solution.

### 3. Defer broad training and bigger search budgets

Two map-to-LLM experiments found no exact gains. Equal-budget restarting found 18 exacts versus 17, all already recorded. These results do not establish that learning can never help; they do show that another prompt pass or budget increase is a weak next bet compared with delivery and new targeted transformations.

Training becomes justified when a specific residual class has trustworthy edit/effect examples and the learned proposal policy beats existing deterministic repair on untouched real functions at equal budget. Preserve a separate generalization evaluation; do not consume held-out source as solver input.

## Conversation trail

Claude project logs: `C:/Users/grant/.claude/projects/c--Code-gameDecomp/`.

- `4d92dd9a-c93e-45a6-a506-d4053c36ab17.jsonl`: September 21 intake/control audit and contaminated-draft discovery, notably the final account at line 2458.
- `027bbb40-7387-4866-b70d-5d95596f1189.jsonl`: September 22–24 mechanism coverage, retrodiction, compiler probes, allocator inverter and branch/loop mechanisms; final account at line 8794.
- `2d0a5e52-2bb2-423f-acf7-98750cabef70.jsonl`: September 24–25 binary types, ledger correction, certificate/operand work and deployment restoration; lines 2395, 3648, 4113 and 5105 are key corrections and final outcomes. The file contains later bookkeeping through September 26.

The remaining in-week conversations concern hardware or model availability/inference questions and do not supersede the measured project findings above.
