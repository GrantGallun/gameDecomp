# Width edits: does site-edit retyping close the sign/zero-extension class?

PRE-REGISTRATION, written 2026-09-29 before any run.

## Question

The structural-residual census (`../structural-residual-20260929/`) found 321 unsolved functions whose
best compiling attempt has sign/zero-extension instructions (`sll/sra 16|24`, `andi 0xff|0xffff`) on
one side only. In 299 of them at least one sits in a local or expression; call arguments, parameters
and returns are the sole site in only 16 (`width_sites.py`). `solver/site_edits.py` already owns
this class on paper: it retypes declarations and integer type tokens on the diff-attributed lines. This asks
whether it **fires** on the class, and whether firing closes it.

## Frame

`frame.json` (`make_frame.py`): the 299 functions, each from its best compiling non-exact attempt in
either ledger (289 campaign, 10 KB). Bands by instruction count: 33 small (<50), 109 medium (<150),
157 large+. Assisted sources are included as they are; this measures a mechanism, not a capability
number. Unchanged `site_edits.search` defaults (budget 48, depth 3, per_step 24, beam 3). Attempts
are logged to `/home/grant/decomp/runs/width-edits-20260929/trial.sqlite`, not a ledger. No model,
no reference source.

## Measures

- **Exact**: `workspace.repair_complete` (bytes plus frontend), per band.
- **Fire**: at least one `decl` or `type` edit compiled for the function.
- **Acted on its class**: the minimum extension-unit count over compiled children is below the
  baseline's.
- A declined level-0 receipt is read and classified, never counted as "nothing to do" (fifth rule).

## Predictions

- Fire rate ≥ 80% overall. Below 50% means the mechanism does not reach its own class (site
  ranking or the declaration finder), and that is fixed before yield is read.
- Acted on its class: 30–60% of functions.
- Exact: 3–15 functions, almost all small/medium. 0 means retyping alone rarely finishes a function in
  this class; the extension fault co-occurs with others (the census says so: 91 single-axis
  functions in 842).

A four-function pilot runs first to catch harness faults. Its rows count toward the result unless
the harness changes, in which case they are discarded and rerun.
