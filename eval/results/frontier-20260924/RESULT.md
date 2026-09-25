# Result: the campaign frontier, and which front to push

Read-only over the campaign ledger (`runs/resume-pipeline-20260908/campaign.sqlite`). `frontier.py`, `fronts.py`.

## Frontier
2,113 functions; 952 exact; 1,013 compiled-not-exact (small 289, medium 447, large 277); 48 never compiled;
100 never attempted. Close (>= 95): 243 (small 107, medium 114, large 22); 115 at 99+.
- 30 close functions scored 100 and were byte-identical, failing only relocation-record order
  (`../reloc-pairing-20260924/`; certificate rule now implemented).
- Among close functions, register differences appear in 163 of 243; 56 are register-only.
- Distinct fault families per function (median): small 3, medium 6, large 9.

## Residual composition (`fronts.py`: aligned differing steps of each best attempt)
| | small (266) | medium (441) | large (276) |
|---|---|---|---|
| median differing steps | 10 | 39 | 162.5 |
| median register share of steps | 0.44 | 0.50 | 0.49 |
| median structural steps (instruction added/removed/replaced) | 2 | 8 | 40 |
| structural = 0 | 72 | 58 | 4 |
| structural <= 2 | 146 | 113 | 11 |
| register-only | 32 | 25 | 0 |

## Reading
1. Allocation is about half of every residual at every size; the compiler work is not exhausted. What exists is the
   FORWARD model (uopt trace, 99.9% of allocation decisions). What is missing is the INVERSE: which C edit moves a
   given live range to the target's register. Inverter v1 performed its intended action in 18 of 85 candidates, 0
   exacts; the `selection` census left 47 wrong ranges with no identified cause.
2. **Allocator front = 130 functions (72 small, 58 medium) with zero structural differences**: every instruction is
   already right and only registers/operands differ. 259 with at most 2 structural steps. This is the most reachable
   pool, and it is bounded by the missing inverse, not by types.
3. **Large functions are not mostly allocation fallout**: median 40 structural steps (quartiles 17 / 40 / 75), only 4
   with none. Allocation work alone will not open them; they need structural composition (localized, class-by-class
   acceptance) first, and the allocator after.
4. Where the information is lacking, by front:
   - allocator: the edit -> allocation-effect map. The campaign's own attempts (166,203 rows, many register-changing
     edits with diffs before and after) are the natural source to MINE it from, and a supervised target if a model
     is ever trained (TRAINING.md);
   - large: which C construct produced each structurally wrong block (attribution to line exists; the inverse for
     branch/loop shape is partial: branch_shape covers selects, returns, register locals, struct copies).
