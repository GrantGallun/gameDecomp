# Independent callback reload search

The callback score improved **94.723 to 97.454**, with **76/76 structured differential cases passing**. The unqualified candidate is [10.c](10.c); [replay.json](replay.json) records the full panel.

The productive source shape separates a direct sentinel-head guard from a reload through the insertion pointer, then uses a bottom-tested `for (;;)` traversal. No volatile qualifier is required. This restores the target's second head read, which the previous single `while` traversal reused instead.

Sixty guard/access/traversal combinations were compiled. Integer and volatile access views did not improve the unqualified winner. A further twenty address-materialization spellings in `../callback-finish-reload-v2` were either compiler-inert or worse. No production source, flags, build guards or reference C were changed or consulted.

The residual now has one missing instruction: IDO emits `lui; lw %lo(sentinel+4); nop` for the reload, whereas the target emits `lui; addiu; lw 4(base); nop`. Several global temporary registers also differ. Explicit head-base locals, register qualifiers, pointer/integer indexing, comma expressions and offset materialization did not prevent the relocation folding.

The bounded generator lives in `solver/callback_reload_alternatives.py` and prioritizes the verified unqualified shape. This module recognizes the complete known traversal body before replacement. Focused tests check exact reproduction, budget, edit scope and rejection of unrecognized body side effects.
