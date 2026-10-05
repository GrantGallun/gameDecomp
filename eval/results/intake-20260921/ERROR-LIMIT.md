# The histogram was measured through clang's 20-error ceiling

`FAULT-HISTOGRAM.md` fixed two windows — the runner's six-line one and `errors[:40]` — and the class set
it reports is still a window. The cap is **clang's own `-ferror-limit`, which defaults to 20**, and
`solver/frontend_check.recipe` passed no such flag. Every defect-class set in the frame was built from
at most 19 errors, and every one of them was labelled complete.

## The fingerprint, from the receipt that already exists

Read straight out of `wide-intake-globals.json` (780 per-step frontend reads over the 200 states):

| | |
|---|---:|
| observations | 780 |
| **highest error count ever observed** | **19** |
| observations sitting at exactly 19 | **352 (45.1%)** |
| states with at least one read pinned at the ceiling | **118 of 200 (59%)** |
| observations reporting `errors_truncated` | **0** |
| observations flagged `classes_from_window` | **0** |

Not one read in the entire frame ever exceeded 19 errors. For a 200-state panel of non-compiling
candidates that is not a property of the candidates; it is the ceiling, and 45% of the frame is sitting
on it while reporting a complete observation.

## The class that proves it is a class and not a count

30 `undeclared_N()` calls followed by one `p->no_such_member`, real clang 20.1.2:

```
default            19 errors, then "fatal error: too many errors emitted, stopping now"
                   -> `no member named` NEVER APPEARS
-ferror-limit=0    31 errors
                   -> `no member named 'no_such_member' in 'struct S'` is there
```

A whole class was invisible because of **where it sat in the file**. That is not neutral across the
taxonomy: declarations are at the top of a translation unit and member accesses are below them, so the
classes systematically truncated away are exactly the late ones.

## What this means for the numbers in FAULT-HISTOGRAM.md

Read the affected rows again with the ceiling in mind:

- **`undeclared-member`: visible at step 0 in 27, cleared in 0.** A class that mostly lives past error
  19 will look both rare and unclearable, because clearing it is not what makes it appear.
- **`member-on-typed-pointer`: 62 states, depth 1, "only visible after a repair."** Same shape.
- **`masking_fanout_any_attempt` ≈ 1.02 / 0.98 / 0.88 — the centrepiece finding.** Under a hard cap of
  19, removing errors *mechanically admits* previously-excluded ones. "Each repair clears one wall and
  the next becomes visible" and "you freed slots under a ceiling" produce the same number. The finding
  may still hold; it is **not established by this measurement**, and the conclusion drawn from it —
  that the class count cannot show progress — now has two possible causes rather than one.
- **`classes_masked_then_revealed`: unclassified 70, member-on-typed-pointer 31, undeclared-member 22.**
  These are the counts most exposed to the artifact.

The class-set size distribution (20/37/64/40/20/16/3) is a **lower bound** on every state that was
pinned at 19 — which is 118 of the 200.

## The fix

| where | change |
|---|---|
| `solver/frontend_check.py` | the recipe puts `-ferror-limit=0` on the checker command |
| `solver/frontend_diagnostics.py` | `_LIMIT` reads the checker's own `fatal error: too many errors` notice — a `kind` that `_ERROR` never matched, so the cut left no trace — and `errors_truncated` now carries that answer instead of the literal `False` |
| `eval/intake_probe.py` | `classes_from_window` is true when EITHER the text or the error list was cut; it watched only the text |

Tests: `tests/test_frontend_error_limit.py` (4). The first asserts the fix **fires on its motivating
residual** against real clang, per CLAUDE.md's fifth rule, rather than only asserting it declines
elsewhere. Regression: 115 passed, 2 skipped across every `test_frontend*`, `test_intake*` and
`test_fault*` suite.

## RE-MEASURED — the control held and the ceiling is gone

`wide-intake-ferrorlimit.json`, same frozen frame, 419.9s against the previous 419.6s.

**The control, which is what makes this an instrument change and not a candidate change:**

| acceptance level | globals | ferrorlimit |
|---|---:|---:|
| IDO compiled | 38 | **38** |
| IDO + frontend | 21 | **21** |
| compiled AND frontend | 20 | **20** |
| byte-exact | 2 | **2** |

Not one moved. The candidates are identical; only the observation changed.

**The ceiling:**

| | globals | ferrorlimit |
|---|---:|---:|
| highest error count ever observed | **19** | **278** |
| reads at or above 19 | 352 | 366 |

19 → 278 is the whole finding in one number.

**The distribution got worse, as predicted — that is the instrument telling the truth:**

| classes on final candidate | globals | ferrorlimit |
|---|---:|---:|
| 0 | 22 | 22 |
| 1 | 43 | 43 |
| 2 | 58 | 43 |
| 3 | 30 | 25 |
| 4 | 27 | **39** |
| 5 | 16 | **19** |
| 6 | 4 | **9** |
| **states with ≥3 classes** | **77** | **92** |

34 states moved and **every one moved in the same direction** — `classes_added_in_total` against the
old receipt is **empty**, while 48 class-instances were previously invisible (16
`incompatible-int-pointer`, 13 `undeclared-identifier`, 6 `member-on-typed-pointer`, 4
`undeclared-member`, 4 `unclassified`, 3 `incompatible-pointer`, 2 others). A one-directional move is
what a lifted ceiling looks like; a mixed one would have meant something else changed.

### And the fix exposed its own mirror defect

With the cap lifted, 83 reads tripped `classes_from_window` — all of them with **complete** error lists.
`_diagnostic_chain` was labelling off `diagnostics_truncated`, the 16 kB cut to the *stored text*, but
`analyse` parses `errors` from the whole output **before** trimming, so a text cut cannot shorten the
list. The label was wrong in both directions: silent on the real cap, noisy on a harmless one. It now
reads `errors_truncated` only — the runner's own OR of the checker's answer with its `max_errors`
window — and the trace carries `diagnostics_truncated` separately so the two can be told apart in a
receipt, which is what made this take a re-measure to see.

`tests/test_frontend_diagnostics.test_a_class_set_is_labelled_when_the_checker_text_was_cut` asserted
the old proxy and was rewritten rather than deleted: its intent (a short class set must be labelled) was
right, its mechanism was not, and its stub — `error_count` 9 against a one-entry list with
`errors_truncated: False` — is not a state the real runner can produce.

## What has to happen next, and what must not

**Re-measure the frame before any of the ordering above is acted on.** The blocker graph, the repair
order and "this is the blocker to pause for" are all downstream of the truncated class sets.

The acceptance levels are the control: **IDO 38 / IDO+frontend 21 / byte-exact 2 must not move.** This
is an instrument change, exactly like the two `FAULT-HISTOGRAM.md` already records, and if the
acceptance numbers move then something other than the instrument changed.

Expect the class-set distribution to get **worse-looking** — more classes per state, a longer tail.
That is the instrument telling the truth for the first time, not a regression, and it is the fourth
time on this panel that a number got worse because it got honest. A class count is still an
observation and still must never be a reward or a distance label.

One cost, stated: `-ferror-limit=0` makes clang emit every error on badly broken candidates, so the
checker does more work per call. It is bounded by the existing 60s timeout.
