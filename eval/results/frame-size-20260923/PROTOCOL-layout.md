# Protocol H15b: IDO 5.3 frame layout (written before layout.py ran)

Rule under test, fitted on the seven constructs in PROTOCOL-constructs.md (so those do not count as evidence):

1. Each declared local takes a virtual offset below the frame top in declaration order: size rounded up to 4, the
   offset aligned to the type's alignment (8 for double), whether or not the local ends up in a register.
2. locals = align8(the depth of the deepest local that is memory-resident: address taken, or spilled to its home).
3. saves = align8(4 x saved registers); ra is at the top of the save area, s-registers descending below it.
4. outgoing = align8(max(16, 4 x the widest call's argument words)); 0 for a leaf with no calls.
5. frame = outgoing + saves + locals; the locals area is at the top, the saves just below it.

Predictions, on new constructs (all in `syn_f(int n)`; `x = syn_g(n); syn_g(x);` unless stated):

| id | construct | frame | slots |
|---|---|---|---|
| P1 decl_swap | `int y; int x;` with `syn_p(&y)` | 32 | y@28, ra@20 |
| P2 unused_between | `int x; int u; int y;`, u unused, `syn_p(&y)` | 40 | y@28, ra@20 |
| P3 short_addr | `int x; short s;` `syn_p(&s)` | 32 | s@24 (-8, size rounded to 4), ra@20 |
| P4 double_addr | `int x; double d;` `syn_p(&d)` | 40 | d@24 (-16), ra@20 |
| P5 sixth_arg | `syn_h6(1,2,3,4,x,x)` after `syn_g(x)` | 40 | args@16,20; ra@28; x@36 |
| P6 callee_saved | loop `for (i=0;i<n;i++) syn_g(i);` in i, n registers | 32 | ra@28, s1@24, s0@20 |
| P7 two_arrays | `int x; char a[5]; char b[3];` `syn_p(a); syn_p(b);` | 40 | a@28 (-12), b@24 (-16) |

P6's register choice (s-regs vs home spills) is not predicted by this rule; if the compile keeps i/n in homes, P6 is
reported as **untestable for rule 3**, not as a pass or fail. **Confirmed** if every testable frame and every
predicted slot match. Any miss is reported with the observed layout, and nothing is fitted to it before rerunning on
fresh constructs.

## Result (layout.json): not confirmed, 21/22

Every frame size and save slot matched. The miss: `short s` sat at 26 (-6), not 24. The original table said -6; I
"corrected" it to follow rule 1's size-rounding before running, and the correction was wrong. Arrays still landed on
4-byte boundaries (`char a[5]` at -12, `char b[3]` at -16, `char buf[6]` at -12), so the refined rule is:

## H15c (pre-registered, fresh constructs, layout2.py)

Rule 1 becomes: a scalar local's offset is aligned to its own size (char 1, short 2, int/pointer/float 4, double 8);
an aggregate (array or struct) is aligned to max(4, its alignment). Rules 2-5 unchanged. Each case below is
`syn_f(int n)` with `x = syn_g(n); syn_g(x);` and `syn_p` on each addressed local, in declaration order:

| id | locals (all addressed except x) | frame | addressed slots in call order |
|---|---|---|---|
| Q1 | `int x; char c;` | 32 | c@27 (-5) |
| Q2 | `int x; short a; short b;` | 32 | a@26 (-6), b@24 (-8) |
| Q3 | `int x; char c; int y;` | 40 | c@35 (-5), y@28 (-9 aligned to 4 = -12) |
| Q4 | `int x; short arr[3];` | 40 | arr@28 (-10 aligned to 4 = -12) |
| Q5 | `int x; Sh3 t;` with `typedef struct {short a,b,c;} Sh3;` | 40 | t@28 (-12) |
| Q6 | `int x; char c; double d;` | 40 | c@35 (-5), d@24 (-13 aligned to 8 = -16) |

(Corrected before running: the first draft of this table had arithmetic slips in Q3 and Q6.) Alternative reading for
Q5 only: if a struct takes its natural alignment (2), t@30. Confirmed if all six frames and slots match.
