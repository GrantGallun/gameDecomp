# Protocol H17: which uopt locals own frame bytes? (written before vreg_frames.py ran)

Origin: finishCurrentRdpTask's candidate lists `isvar M 4 -4` (sp1C, address taken) and `isvar M 4 -8vreg`
(temp_v0, coloured v0). Its locals area is 16 bytes in both declaration orders; inlining temp_v0 removes the vreg
line and the area drops to 8. The synthetic P6 (`i` in s0, no memory local) had no locals area. H15c's depth clause
explains neither.

Data: the uopt trace census (matched SBK1 corpus, traces of the compiles whose objects are byte-exact to the
target), with each target object's frame. Per procedure: observed locals area = frame - align8(outgoing) -
align8(4 x saved int regs + 8 x saved float regs), outgoing by the frame_size amendment (0 in a leaf). From the
isvar lines: mem_depth = max -offset over M lines without `vreg`; vreg_depth = the same over M lines with `vreg`.

Candidate formulas for the locals area (all pre-registered here):
  F0 (H15c)       align8(mem_depth)
  F1 (all)        align8(max(mem_depth, vreg_depth))
  F2 (split)      align8(mem_depth) + align8(vreg_depth - (the vreg's region begins below the memory locals))
                  implemented as align8(mem_depth) + align8(vreg_depth) when mem_depth > 0, else 0
  F3 (split-any)  align8(mem_depth) + align8(vreg_depth)
Procedures are split by a fixed hash of the name into fit (even) and check (odd) halves. The formula with the best
fit-half hit rate is reported on the check half; **confirmed** only if it explains >= 90% of the check half AND
every miss class is listed. The observed-area component itself is only as good as the outgoing and save rules;
procedures where observed area < 0 are reported as instrument failures, not counted.

## Result v1: not confirmed (best F0, 65.0% on the check half), and the instrument was faulty

vreg_examples.py: (1) the frame_size outgoing amendment treats every store below `frame - saves` as an outgoing
argument slot, which assumed saves at the top of the frame; under ido53-frame-layout locals are on top, so
`sw ra,0x14(sp)` in a 0x20 frame (finishCurrentRdpTask's own target) was read as outgoing 24 and the locals area as
0. (2) 118 procedures with no isvar at all had frame 32 with s0@0x18 and ra@0x1c: the save region is not
align8(4 x saves) (8 bytes pad below it), a save-area sub-gap of its own.

## AMENDMENT A1 (labelled; before rerunning)

Measure the locals area without the outgoing/save models: locals_area = frame - align8(end of the highest save
slot) where save slots are `sw ra|s0-s8|fp` and `sdc1/swc1 $f20-$f30` to sp in the prologue; a procedure with no
save slot is skipped (reported count). Same formulas, same halves, same 90% bar. The save-region rule is recorded as
open, not fitted here.

## Result A1: not confirmed (F0 75.6% check), instrument clean (0 failures)

No-locals procedures: 560/573 area 0. Every miss class has vreg locals owning bytes. Exploration on the FIT half
only (vreg_features.py): an uncoloured vreg piece in 3/300 area-0 procedures vs 45/103 area>0 ones; "listed but no
live range" does not separate (211/300 vs 77/103).

## AMENDMENT A2 (labelled; formulas added after fit-half exploration, check half not yet looked at)

  F5  align8(max(mem_depth, depth of vreg locals with any uncoloured piece))
  F6  align8(max(mem_depth, depth of vreg locals with any uncoloured piece OR coloured only caller-saved (v0-t7)))
Selection on the fit half, reported on the check half, same 90% bar.

## Result A2: not confirmed; F5 is the best partial rule (fit 80.0%, check 79.3%, vs F0 75.6%)

F6 (all caller-saved-only vregs own a home) fell to 59.7%: most such locals do not. The check-half misses are
dominated by vreg locals coloured caller-saved with no uncoloured piece that nevertheless own a home (+8 per ~1-2
such locals, and a +96 family with 19). finishCurrentRdpTask's temp_v0 (coloured v0) is in this class. Selector
unknown. Next test, not run: whether the owning locals are exactly those whose range spans a call (a caller-saved
colour across a call needs a home to survive it), which needs call positions per block from the trace.
Instrument lesson recorded: region sizes must be read from the slots, never from a model of the other regions.
