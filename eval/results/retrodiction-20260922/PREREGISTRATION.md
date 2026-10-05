# Retrodiction: does exploring potential rediscover mechanisms we already have?

Written before any analysis in this directory ran.

## Hidden
`owner:drop_mask`, `owner:layout`, `owner:per_object_layout`, `owner:global_load_signedness`. Every recorded
node produced by one of them, and every descendant of such a node, is removed from the data the analysis sees.

## Blind analysis (`signatures.py`)
Residual signatures are generic, built from instruction structure only, on `diffrepair.aligned_pairs`:
same opcode with exactly one differing field kind -> `field:{register|offset|immediate|symbol|branch}`;
different opcodes -> `opcode:{a}/{b}` (sorted); unpaired instruction surplus -> `extra:{op}` (candidate only)
or `missing:{op}` (target only). Per signature: functions where it occurs (demand), functions where a
remaining mechanism reduced it at least once (coverage), whether the diff states a source-expressible fix
(offset, immediate, symbol, load/store width or signedness opcode, extra instruction = delete; registers,
branch targets and missing instructions are not), and how often `source_attribution` locates it.
Potential = uncovered functions, counted only for stated, source-expressible, locatable signatures.

## Hidden mechanisms' documented targets, as generic signatures
- `owner:drop_mask` -> `extra:andi`
- `owner:layout`, `owner:per_object_layout` -> `field:offset`
- `owner:global_load_signedness` -> `opcode:lb/lbu`, `opcode:lh/lhu`

## Success criteria
1. **Flagged:** each hidden target signature ranks in the top quartile of potential among signatures present
   in at least 5 functions.
2. **Derived:** a single generic mechanism (`derive.py`: put the diff's stated target value on the token of the
   attributed C line that carries the candidate's value; for an extra instruction, delete its operator; for a
   width/signedness opcode, retype with the ISA->C type map) proposes, on the parents where a hidden mechanism
   improved or reached exact, a candidate for the same residual.
3. **Recovers:** compiled, those derived candidates reach at least the hidden mechanism's score on a majority
   of those parents, and the hidden mechanism's exacts.

A pass on 1 but not 2-3 means potential finds where a mechanism belongs but not what it should do.
