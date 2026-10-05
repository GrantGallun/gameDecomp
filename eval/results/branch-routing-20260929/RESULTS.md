# Branch-shape routing — results

Pre-registration: `PREREGISTRATION.md`. Raw: `results.jsonl` (75 functions, 0 harness errors). Attempts:
`/home/grant/decomp/runs/branch-routing-20260929/trial.sqlite`.

| | predicted | result |
|---|---|---|
| exact | 1–5 of 75 | **0** |
| functions improved (lower `site_edits.gradient` than re-scored baseline) | 20–40% | **52 of 75 (69%)** |
| variants that don't compile | < 10% | **0 of 202** |

| family | variants | improved | exact |
|---|---:|---:|---:|
| split_merge | 104 | 36 | 0 |
| at_inline | 44 | 31 | 0 |
| select_else | 34 | 12 | 0 |
| m2c_struct_copy | 16 | 1 | 0 |
| empty_then_return | 3 | 3 | 0 |
| dup_return_merge | 1 | 1 | 0 |

## Reading

The routing loss is real: the generators are valid and helpful on states the campaign never
applied them to. But one control-flow repair doesn't finish a function, just as one width repair
doesn't (`../width-edits-20260929/`). Both point to composition inside one search, which is what
`../composed-edits-20260929/` tests.
