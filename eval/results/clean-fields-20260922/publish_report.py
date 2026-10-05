"""Render audited measurements and explicitly scope the repaired entry points."""
import json
from pathlib import Path

OUT=Path(__file__).resolve().parent
load=lambda name:json.loads((OUT/name).read_text())
s=load('summary.json'); pre=s['before'];post=s['after']
before=load('inventory-before-reviewed.json')['frontend_classes']
classes=sorted(s['remaining_frontend'].items(),key=lambda item:item[1]['functions'],reverse=True)
metrics=[('IDO compiled','ido_compiled'),('Frontend passed','frontend_passed'),
    ('Both passed','both'),('Object exact','exact'),('Frontend diagnostics','errors')]
table='\n'.join(f'| {label} | {pre[key]} | {post[key]} |' for label,key in metrics)
blockers='\n'.join(f'| {name} | {before.get(name,{}).get("functions",0)} | {data["functions"]} | {data["diagnostics"]} |'
                   for name,data in classes)
confirm=load('exact-confirmation.json')
exacts='\n'.join(f'- `{r["function"]}`: independently certified; {r["prior_research_exact_attempts"]} prior research exact attempts.' for r in confirm)
if not exacts:exacts='No additional exact objects in this loop.'
focus=(OUT/'tests-focused.log').read_text().strip().splitlines()[-1]
full=(OUT/'tests-full.log').read_text().strip().splitlines()[-1]
comparison=load('tests-comparison.json')
prior_recheck=load('tests-prior-recheck.json') if comparison['new_failures'] else {'failures':[]}
prior_failed={(r['test'],r['phase']) for r in prior_recheck['failures']}
assert set(map(tuple,comparison['new_failures'])) <= prior_failed,comparison
test_note=(f"{len(comparison['new_failures'])} additional failing identities reproduce against the prior frozen implementation: "
    "Windows process-control tests cannot execute wsl.exe inside this WSL environment. "
    "The other failing identities are unchanged." if comparison['new_failures'] else
    'No new failing test identities.')
assert load('tests-focused.json')['exit_code']==0
assert load('working-tree-verification.json')['all_measured_modules_match']
text=f'''# Frozen storage repair and routing loop — September 22, 2026

Same 200 header-assisted candidates as `clean-types-20260922/final.json`.
These are isolated cohort counts, not production SOLVED totals or recovered-layout claims.

| Measurement | Before | After |
|---|---:|---:|
{table}

{s['changed_sources']} retained sources improved; {s['fewer_errors']} have fewer frontend errors.
No incumbent exactness, IDO pass, frontend pass, complete error count or compiled similarity regressed.

![Overlapping frontend and object failure histograms](histogram.png)

## Reusable machinery and the routing gap

- `solver/global_field_view.py` proposes byte lvalues for diagnosed named globals.
  Included declaration, source site, target symbol/offset, direction and width
  must agree. It does not invent field names or complete record layouts.
- `solver/stack_scalar_arrays.py` proposes contiguous indexed word storage with
  witnessed stack slots and merges supported overlapping scalar aliases.
  Stack-name correspondence is a hypothesis, still subject to compilation and
  object comparison. Ambiguous syntax, shadows and access widths are declined.
- Both owners run in the normal bounded intake sequence and in
  `solver/modelrepair.search(resilient=True)`. The campaign path also now tries
  prior global-scalar, header-signature, call-arity and frontend-cast owners that
  had only been connected to intake. Existing void/ABI owners get refreshed
  complete diagnostics. Children use ordinary `workspace.score` and attempt logging.
- The real native compiler pilot invokes `modelrepair.search` with zero model
  calls and takes a previously blocked function through IDO and frontend checks.
  Fixture entry-point tests verify actual proposal generation, scoring, parent
  receipts and retention. See [pilot.json](pilot.json) and
  `tests/test_modelrepair_frontend_routes.py`.
- No function-name branches or expected-output substitutions were added to the
  production generators. The compiler/object certificate adjudicates candidates.

This closes a real implementation gap: earlier measured intake gains did not all
reach campaign normalization. The separate scripted tool-agent action catalog is
not expanded here. Existing frozen campaign runtimes are not automatically
updated by working-tree changes; this loop does not claim a deployment.

## Current blockers

| Frontend class | Functions before | Functions after | Diagnostics after |
|---|---:|---:|---:|
{blockers}

Categories overlap. {s['remaining_member_functions']} distinct functions still
have member/access representation blockers. Diagnostics are observations, not
independent causes or a distance to a match. Multiple errors can share one cause;
removing one class can reveal another.

Disjoint stages: `{json.dumps(s['stages'],sort_keys=True)}`.
Compiled nonmatches appear only after compiler admission; growth in that category
can accompany progress. Object axes overlap as well.

Sole remaining frontend classes: `{json.dumps(s['sole_frontend_classes'],sort_keys=True)}`.
Concrete source sites, conditional owner routes and close object residuals:
[next-frontier.json](next-frontier.json).

## Exact results

{exacts}

An object certificate covers compared sections/relocations under the recorded
compiler environment. It is not a whole-ROM integration certificate. Prior
research matches are labeled recovered, not new global discoveries.

## Verification and receipts

- Fresh 200-function native WSL baseline reproduced all {pre['exact']} prior exact objects.
- Frozen intake replay: {s['search_attempts']} logged children, three rounds /
  twelve children / beam width three per function. Stops:
  `{json.dumps(s['search_stops'],sort_keys=True)}`. Budget stops are not convergence.
- Object pass: {s['object_states_examined']} compiled nonmatches examined,
  {s['object_attempts']} children from the existing shared rewrite catalog and
  bounded existing register/order generators. No new backend mutation here.
- Focused suite against the frozen implementation: `{focus}`.
- Full suite: `{full}`. {test_note}
  {comparison['failed_reports']} failure reports remain. See [tests-comparison.json](tests-comparison.json)
  and [prior-code reproduction](tests-prior-recheck.json). The final additional
  compiler-rejection guard test is included in the focused run.
- {s['logged_attempts_all_experiments']} private attempts, including pilots and
  failed candidates; {s['audited_search_nodes']} search nodes and
  {s['audited_object_nodes']} object children audited. Source hashes, parent
  edges and final source/verdict bindings passed.
- Binary evidence: {s['evidence']['rows']} immutable copied rows, SHA-256
  `{s['evidence']['sha256']}`. No production KB inference mutation.
- Four disjoint workers used the same [frozen code snapshot](frozen-code.json).
  Measured module hashes match the working tree. Candidate files remain under
  `states-frozen/`; native workspaces and attempt DB are under
  `/home/grant/decomp/experiments/clean-fields-20260922/`.

Authoritative artifacts: [final.json](final.json), [summary.json](summary.json),
[accepted-lineages.json](accepted-lineages.json), [attempt audit](attempt-log-check.json),
[code audit](code-verification.json), [exact confirmation](exact-confirmation.json).
No held-out game function bodies were supplied to proposals, prompts or the KB.
'''
(OUT/'REPORT.md').write_text(text,encoding='utf-8')
print(table)
