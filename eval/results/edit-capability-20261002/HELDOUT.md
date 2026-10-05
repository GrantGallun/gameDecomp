# Held-out set (frozen 2026-10-02, before any further development)

`~/decomp/experiments/edit-capability-20261002/heldout.jsonl`, sha256
`850bf1ad2527f2466123cb730921f599c21309fe7785bb1ab207a3dfb66489ff`.

42 cases, planted by `python3 run.py plant --per-class 6 --out heldout.jsonl --exclude-from cases.jsonl` on the
121 clean binary-context exact functions that the development set (`cases.jsonl`, 51 cases) does not use. No
function appears in both. Tally: `plant_tally_heldout.json`. (IDO normalized every commute, cmp_mirror, if_invert and
temp_return perturbation on these functions, so those classes have no held-out cases.)

Rules for everything built after this point:
- Develop and debug on `cases.jsonl` only. Held-out cases are not read, printed or inspected individually.
- Held-out is scored once per frozen mechanism version; the number reported is the held-out number.
- Baseline before development: `search_heldout_pre.jsonl` (site_edits + operators, no model): **35/42**
  (drop_stmt 0/6, decl_width 5/6, every other class 6/6).
- A first full-system run (`heldout_v1`) was started on code with a ranking bug that a development fire test then
  found (widening hint applied before grouping). It was killed and its partial output deleted unread; the scored
  run is `system_heldout_v2` on the fixed code.
- Disclosure: that run's log printed held-out function NAMES and winning-edit labels (enum_search printed ids). No
  held-out source or diff was read and nothing was developed against them; the harness now prints class only.

## Recertification (2026-10-03, after an external audit)

The v2 verdicts compared masked listings (`mine.mask` hides relocation symbols, so `return ga;` and `return gb;`
compare equal). `system.py --recert` replays the run (generation cache only, no GPU) and certifies every masked match
with `solver.byte_certificate.certify` against the workspace's ROM-extracted `target.o`. It replays the CURRENT code:
`solver/site_edits.py` changed after v2 (later widening-hint fix; opt-in reorder/escalate hooks), so it is a
recertified re-run, not a byte-identical replay (dev: 47 of 51 rows identical; 4 width cases found a cheaper winner).

| panel | masked-listing matches | certified | originals that certify |
|---|---|---|---|
| dev (51) | 51 | 47 | 47 |
| held-out (42) | 41 | 41 | 42 |

The 4 dev rows that do not certify are all `initControllerPakRaceRecordSaveExitMessage`, whose ORIGINAL source does
not certify against `target.o` either: that function is not a valid planted case. Every masked match on a valid case
certified. Rows: `system_dev_v2_recert.jsonl`, `system_heldout_v2_recert.jsonl` (originals untouched).
