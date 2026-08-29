# Ideas

Living queue for Brain Workspace ideas that have not been tested yet.

Update rules:
- Add only actionable or recurring ideas.
- Keep untested hypotheses here until evidence is gathered.
- When an idea is tested, add the result to `hypothesis-graveyard.md` and update or remove the active idea entry.
- Avoid churn; do not log obvious implementation minutiae.

Created: 2026-08-29

## Active Ideas

### IDEA-20260829-01: Evidence-linked hypothesis DAG
- Status: Active
- Source: Current Codex session
- Summary: Replace prose-only revisions and manual reconciliation with scoped claims linked to exact experiment fingerprints, attempt IDs, code revisions, dependencies, contradictions, and invalidation state. Split mechanism, operationalization, and generalization claims so one-function success cannot silently become a general CONFIRMED result.
- Next test: Migrate five high-value entries, including trace-directed-call-repair and bank-reconciliation-needed, then inject one methodological invalidation and verify dependent claims become needs_revalidation.
- Links: None
- Last touched: 2026-08-29

### IDEA-20260829-02: Structured stage-event ledger and failure signatures
- Status: Active
- Source: Current Codex session
- Summary: Persist every pipeline boundary as a typed event with input/output hashes, duration, stable status code, and details. Generate trace and corpus audit views from this ledger, then cluster normalized compiler and object-diff signatures across functions.
- Next test: Instrument one frozen eight-function replay and require tools/trace.py and tools/audit.py to reproduce their findings without parsing human-formatted logs.
- Links: None
- Last touched: 2026-08-29

### IDEA-20260829-03: Prevalence-first cost-aware experiment gate
- Status: Active
- Source: Current Codex session
- Summary: Before spending GPU time, measure whether a proposed detector or repair fires on the corpus, verify the causal surface on stored candidates or compiler microprobes, and rank experiments by expected affected functions and match gain per wall-clock cost.
- Next test: Score the last ten experiments retrospectively and test whether the gate would have killed the no-surface struct-repair run and low-prevalence byte-cast idea before GPU execution.
- Links: None
- Last touched: 2026-08-29

## Candidate Hypotheses

## Open Questions
