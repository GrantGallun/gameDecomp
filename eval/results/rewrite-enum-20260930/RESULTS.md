# Rewrite generation (Engine G): results

Pre-registration and both amendments: `PREREGISTRATION.md`. Code: `eval/rewrite_enum.py` (generation),
`solver/term_rewrite.py` (tree matching and application), `rule_miner._generated` (ranking), `eval/rule_mine.py table
--with-generated` (merge). Tests: `tests/test_term_rewrite.py`, `tests/test_rewrite_enum.py` (IDO probe test runs in WSL).

## Verdict

**Negative. Engine G generates real vocabulary, but on the development frame it made the search worse (1 win, 4
losses), so the sealed 50 was not run and G is opt-in.** The default table is back to Engines A and B.

## Pipeline yield

| step | result |
|---|---|
| shapes | 1,053 exact sources, 465 shapes, 172 in at least 3 functions |
| siblings | 513,162 enumerated terms, 623 pairs after pruning padding, split constants and reused operands |
| IDO probes (4 contexts) | 374 pairs compile differently, 249 inert, 0 broken |
| validation on exact functions | 576 functions, 5,716 applications, 99.6% compiled, 2,250 changed the listing |
| rules | 427 directions validated, **157 usable** |

G2 (prediction: at least 50 usable, and at least 20% pruned as inert in context): **pass**. 157 usable; 125 of the
300 directions with at least 3 compiles changed the listing less than 5% of the time (42%). A spelling that compiles
differently in isolation often does not inside a real function.

Three sampler holes were caught before any rule was used, each by a known-answer test:
- `x == N` read as `x == 0`: random rows rarely make operands equal.
- `(u8)x == 2` read as `x == 2`: no values agreed only in the low byte.
- `- -x` rendered as `--x`.

## Search tests (development frame, the T4 50, against recorded U2)

| arm | change | G edits tried | wins / losses / ties | compiles |
|---|---|---|---|---|
| G3 | table with G | 8, in 4 functions | 1 / 1 / 48, p = 0.75 | 1,824 vs 1,824 |
| G3b | + 2 reserved slots | 17, in 5 functions | 1 / 1 / 48 | 1,824 |
| G3c | + cap counts rules that apply (bug fix) | 117, in 37 functions | **1 / 4 / 45, p = 0.97** | 1,752 vs 1,824 |

- **G3 and G3b are void as tests.** `_generated` capped the scored entries at 24 before checking whether they applied,
  so G proposed nothing on most functions. The first diagnosis blamed ranking; tracing the lane (`trace_lane.py`)
  found the bug. It was the silent decline again: a generator that returns nothing looks like one with nothing to do.
  A regression test now pins it.
- **G3c is the real test.** G fired in 74% of functions (prediction: at least 50%, met), and wins > losses was not met.
  Its one win (initRaceHud) came through a G edit (`E0 + E1 -> E1 + E0`). The four losses are functions where the
  displaced Engine A/B proposals had found the better state. Mean best score -0.03, 4% fewer compiles.
- Under the pre-registered rule, G4 (sealed 50) is not run. The sealed frame stays unspent.

## Reading

Engine G answers the question the rule-miner results left open: is the ceiling vocabulary? For expression spelling,
no. G's 157 rules are operand order, comparison forms, negation and increment spellings. The search now reaches them on
most functions, and they rarely move the gradient further than what A and B already offered. The measured stall
reasons on these frames are statement and control-flow shape (see `../composed-edits-20260929/RESULTS.md`), and G has
no statement-level vocabulary. This is an inference from the classes, not a separate test.

Not tried, and still open:
- Signed-only identities (`x << 16 >> 16` vs `(s16)x`), which need operand types at the match site.
- Statement-level enumeration: the same recipe over statement sequences.

## Files

`shapes.json`, `pairs.json`, `probed.json`, `validate.jsonl`, `rules.json`; `mined_rules.with-g.json` is the table G3
to G3c ran with. `diagnose_g3.py` / `.json` and `trace_lane.py` are the diagnosis. The runner is `run_g.py`, and each
arm has its own trial DB under `~/decomp/runs/rewrite-enum-20260930/`.

Rebuilding the default table reproduced every A/B entry except the last tie of two profiles: `most_common` over sets
orders ties by hash seed. Harmless for scoring. Noted because it means the table is not bit-reproducible.
