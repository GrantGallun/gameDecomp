# Mapping C edits to diff effects

The compiler's own line records (`source_attribution`) put every differing instruction on a C line; every recorded
search edge puts a C edit on lines. Joined over 11,378 deduplicated edges (`locality.py`, protocol first):

| Edit | Edges | Improve rate |
|---|---:|---:|
| touches a line a residual instruction is attributed to | 6,072 | **9.2%** |
| touches none | 5,306 | 3.6% |

L1 supported (2.56x). Blind statement families almost never help away from faulty lines: stmt_order 0/358,
commutative 0/658, stmt_move 7/762. Declaration edits (L3, `decl_locality.py`) also do better when the declared
variable is used on a faulty line (local_type 3.0% vs 1.1%; decl_order 0.99% vs 0%), but most already are.

`solver/edit_locality.py` skips blind statement edits that touch no faulty line (needs a source-bound attribution).
Population rerun (`../locality-population-20260923/`, code-v9 vs narrow): **16 vs 16 exact, 0 losses**, 5 better /
1 worse. The filter fired (~490 blind edits skipped: commutative -221, stmt_move -182, stmt_order -87), and the
freed compiles went to other blind families (local_type +173, decl_order +162). Kept (0 losses).

What this says: the map is real and the search now spends less on edits away from the faults, but budget is not
what stops the unsolved functions. Once a node's targeted candidates are used up, what remains is low-yield, so
freed compiles have nowhere useful to go. The limit is the repertoire for the "needs a model" classes (201 of 208
unsolved functions carry at least one). The map's next use is as supervision: (residual on a line, C edit, effect)
triples are the training data for a model that proposes the edit for a residual class no rule covers.

## Making the map actionable: fix lines independently, then merge (`compose.py`)
For every expanded parent with two or more improving children, merge the children's non-overlapping line hunks.
Locality holds for most improving single-line edits (261 of 341 left the faults on other lines unchanged).
167 merged candidates in 52 functions, one compile each: 136 (81%) beat the best single edit they combine, 69 beat
the best of the entire recorded search (34 functions). No exact.

Paired continuation (`continue_arms.py`, 52 functions, 32 more compiles each, code-v9 with evidence): from the
search's best (control) vs from the best merge (compose). Both closed **updateRacePlayerMode40Stun** (header-
assisted, recorded: 377 byte-exact) -- so the closure came from restarting the search at its best (fresh depth),
not from merging. Compose vs control on the rest: 0 gains, 0 losses, 18 better / 7 worse, mean +0.16.
Verdict: merging raises scores reliably and cheaply but produced no match beyond what restarting did; restarting
from the best node is itself worth making standard (a 32-compile search is depth-limited to 4).
