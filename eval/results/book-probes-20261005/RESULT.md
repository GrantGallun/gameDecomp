# Book principles vs IDO 5.3 (batch 1, 2026-10-05)

`python3 -m eval.book_probes` — 19 principles from reverse-engineering and compiler literature (sources in the module
docstring), each turned into minimal C spellings and compiled with the game's own -O2 (game code) and -O1 (libultra)
recipes. Receipt: `probes.json` (every differing pair keeps both listings). `same` = IDO treats the spellings as one
program; `differ` = the spelling is visible in the code.

| principle (source) | book expects | IDO -O2 | IDO -O1 | what it means for matching |
|---|---|---|---|---|
| switch ≡ if-else chain (RE4B, EILAM, KASP) | same below table size | **differ** (3, 5, 8 dense; 4 sparse) | differ | a switch in asm must be a `switch` in C at every size probed |
| switch case order invisible (RE4B, EILAM) | same | **differ** | differ | **case source order is visible** (block layout follows it) |
| `x*10` ≡ shift-add (HD, KASP) | same | **differ** | differ | IDO strength-reduces `x*10` itself (`sll 2; addu; sll 1`), a different decomposition than hand-written shifts: **a shift/add chain in asm means a multiply in C** |
| signed vs unsigned `/ k` (RE4B, HD) | differ | differ | differ | confirmed: the division sequence names the signedness |
| `return a==b` ≡ `?1:0` ≡ if/return (RE4B, EILAM) | same | **differ** | differ | **branch-free `xor/sltiu` means the comparison value itself**; a ternary or if/return branches |
| `a && b` ≡ nested ifs (CIF, EILAM) | same | same | same | confirmed equivalence |
| `a \|\| b` ≡ else-if with the same body | same | **differ** | differ | `\|\|` is visible |
| ternary select ≡ if/else assignment (EILAM) | same | same | same | confirmed equivalence |
| abs / min as ternary ≡ if (EILAM) | same | **differ** | differ | the `if (p<0) p=-p;` spelling is visible |
| for ≡ while ≡ guarded do-while (EILAM, CIF) | same | same | **differ** | equivalent at -O2 (after fixing a probe confound: initialisation order differed) |
| indexed loop ≡ pointer walk (KASP) | same | **differ** | differ | **IDO fully unrolls a 16-trip indexed loop** but not the pointer walk |
| early return ≡ single exit (SAILR) | may differ | differ | differ | return structure is visible |
| forward goto ≡ structured (SAILR) | same | same | same | confirmed equivalence |
| repeated load ≡ temp (CSE; KASP, RE4B) | same | **differ** | differ | IDO reloads a global after a store through a pointer (possible alias), so **a temp is visible**; the same for a global pointer |
| `f*0.5` vs `f*0.5f` (C89) | differ | differ | differ | confirmed: `cvt.d.s` round trip means an unsuffixed constant |
| float `!(a>=b)` vs `a<b` (RE4B, NaN) | differ | differ | differ | confirmed |
| bit-field ≡ shift/mask (MIPS) | same | **differ** (middle field) / same (first) | differ | layout confirmed MSB-first, but IDO reads a 12-bit field with **`lhu` + `andi`** (narrowest load), not a word shift |
| `lo<=x && x<=hi` ≡ unsigned compare (HD) | may fold | differ | differ | IDO does not fold: an unsigned range compare in asm was written that way |
| `-x` ≡ `0-x` ≡ `~x+1` (KASP) | same | same / **differ** (`~x+1`) | same / differ | `~x+1` is visible |
| `a[i++]` ≡ `a[i]; i++` | same | same | same | confirmed equivalence |
| const local ≡ literal (KASP) | same | same | **differ** | equivalent at -O2 only |
| `(s32)` cast on s8/u8 compare (C89) | same | same | same | confirmed equivalence |

Summary at -O2: of 22 rows, the book's expectation held for 12 and failed (wholly or partly) for 10. The failures are the useful part:
switch shape and case order, `||`, boolean materialisation, multiply decomposition, loop unrolling, alias-blocked CSE,
bit-field load width and `~x+1` are all spellings IDO keeps visible, where a reader trained on the general rule would
treat them as interchangeable.

Next: catalogue the confirmed rules in `patterns/catalog.py` (confirmed_on = this receipt) and the equivalences in
`patterns/ido_equivalences.json`; turn the differing pairs into logic-predict training tasks; probe the boundaries
the batch opened (smallest switch that becomes a jump table; trip count at which unrolling stops; which stores
block CSE).
