"""Render the verified experiment metrics without substituting global status."""
import json
from pathlib import Path

OUT=Path(__file__).resolve().parent
load=lambda name:json.loads((OUT/name).read_text())
s=load('summary.json'); pre=s['before'];post=s['after']
confirm=load('exact-confirmation.json')
classes=sorted(s['remaining_frontend'].items(),key=lambda item:item[1]['functions'],reverse=True)
before=load('inventory-before-reviewed.json')['frontend_classes']
metrics=[('IDO compiled','ido_compiled'),('Frontend passed','frontend_passed'),
    ('Both passed','both'),('Object exact','exact'),('Frontend diagnostics','errors')]
table='\n'.join(f'| {label} | {pre[key]} | {post[key]} |' for label,key in metrics)
blockers='\n'.join(f'| {name} | {before.get(name,{}).get("functions",0)} | {data["functions"]} | {data["diagnostics"]} |'
                   for name,data in classes)
new_exact='\n'.join(f'- `{row["function"]}`: independently compiled and object-certified; '
    f'{row["prior_research_exact_attempts"]} previous exact attempts in the read-only research DB.' for row in confirm)
if not new_exact:new_exact='No new exact object in this round.'
full=(OUT/'tests-full.log').read_text().splitlines()[-1]
text=f'''# Frozen type/layout repair round — September 22, 2026

Same 200 header-assisted candidates as `clean-calls-20260922/final.json`.
These are isolated experiment counts, not production SOLVED totals or recovered-type claims.

| Measurement | Before | After |
|---|---:|---:|
{table}

{s['changed_sources']} final sources improved; {s['fewer_errors']} have fewer frontend errors.
No prior compiler pass, frontend pass, exact object, complete error count or compiled similarity regressed.

![Overlapping frontend and object histograms](histogram.png)

## What changed

- Binary-observed scalar loads can repair a header-declared aggregate global used as a bare binary operand. Operator position, operand side, reported type, declaration and unanimous direct zero-offset loads must agree. No field name or struct layout is invented.
- Header-locked function definitions preserve candidate body views through typed local aliases for supported equal-width o32 slots. These are header-assisted ABI hypotheses.
- Fresh complete diagnostics feed both owners through the existing bounded intake sequence. Every child is compiled and logged; the incumbent ratchet decides adoption.
- The separate object pass exercises existing rewrite rules, plus existing compound-assignment, commutative and declaration-order generators on >=99% residuals containing only register/order differences. No new register mutation is implemented here.

Review regressions cover unrelated `sizeof`/member uses on the same diagnostic line, parenthesized and macro-defined local shadows, operand-side/type binding, and binary `&` versus address-of.

## Current blockers

| Frontend class | Functions before | Functions after | Diagnostics after |
|---|---:|---:|---:|
{blockers}

Categories overlap: {s['remaining_member_functions']} distinct functions have member/access-representation blockers;
{s['remaining_header_or_member_functions']} have either those or declaration conflicts.
Diagnostic volume is not a repair distance. A function can require several repairs, and a single shared representation mistake can cause many diagnostics.

Current disjoint stages: `{json.dumps(s['stages'],sort_keys=True)}`.
More compiled nonmatches means more functions reached object comparison; it does not imply a regression.

Sole remaining frontend classes: `{json.dumps(s['sole_frontend_classes'],sort_keys=True)}`.
Concrete sites, prerequisites and close object residuals: [next-frontier.json](next-frontier.json).

## Exact results

{new_exact}

The added `FrandNote` result is a recovered prior research match, not a new global discovery. The clean intake route now reproduces it from this cohort's starting state.

Exactness is the workspace's object certificate, including compared sections and relocations under the recorded compiler environment. It is not a whole-ROM integration certificate.

## Measurement and validation

- Fresh baseline: 200 native WSL builds, reproducing all seven prior exact objects.
- Final intake: 200 functions, {s['search_attempts']} logged children, three rounds / twelve children / beam width three per function. Stops: `{json.dumps(s['search_stops'],sort_keys=True)}`. A budget stop is not convergence.
- Object pass: {s['object_states_examined']} compiled nonmatches examined, {s['object_attempts']} compiled children; at most twelve shared rewrites plus twelve register/order hypotheses per qualifying state.
- Final focused validation: 129 intake/type tests and 3 existing object-generator tests passed against the frozen implementation.
- Full repository suite: `{full}`. All 54 failing test identities match the previous round. This full run preceded the last two narrow guard changes; the final focused suite covers them.
- Audited {s['logged_attempts_all_experiments']} private attempts, including interrupted pilots, and {s['audited_search_nodes']} final intake nodes. Source hashes, parent edges and final verdict bindings passed.
- Read-only binary evidence: {s['evidence']['rows']} rows, SHA-256 `{s['evidence']['sha256']}`.
- Four independent native workers used one frozen implementation snapshot: [frozen-code.json](frozen-code.json). Unrelated concurrent workspace edits caused a strict resume check to reject a serial pilot; final results use `paired-frozen.json`. `paired-confirmed.json`, `paired-validated.json`, and `proposal-census.json` are exploratory artifacts, not final measurements.

Authoritative artifacts: [final.json](final.json), [summary.json](summary.json), [accepted-lineages.json](accepted-lineages.json), [attempt-log-check.json](attempt-log-check.json), [code-verification.json](code-verification.json), [exact-confirmation.json](exact-confirmation.json).

Candidate sources remain under `states-frozen/`. Attempts and native build workspaces are under `/home/grant/decomp/experiments/clean-types-20260922/`. This round does not mutate production KB inference, integrate game C, or deploy the frozen campaign. Held-out reference function bodies were not inputs to these repairs.
'''
(OUT/'REPORT.md').write_text(text,encoding='utf-8')
print(table)
