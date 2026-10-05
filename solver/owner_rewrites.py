"""The diff-driven owners in solver/rewrites.py that the mutation search never called.

`regalloc_mutations.variants` is the candidate stream for every near-miss search (the beam in
`regalloc_search`, the experimental schedulers in eval/). Before this module it reached nine of the
27 `(code, diff) -> [Rewrite]` families in `solver/rewrites.py`: six through
`representation_repairs.residual_evidence` and three through `regalloc_mutations.existing`. The
other eighteen, including `layout_rewrites` (the `solver/diffrepair` offset/width owner), were
reachable only from paths the near-miss population no longer goes through.

Measured 2026-09-22 (eval/results/population-transfer-20260922/): on 132 compiling non-exact roots,
61 had at least one of these owners firing on their own residual. Motivating residual:
dispatchRacePlayerMode30Attack at 99.999, a drafted struct whose fields sit at 0 and 4 where the
diff says 0x280 and 0x302. Every search arm exhausted on it with zero variants; `layout_rewrites`
closes it in one compile.

Each owner applies its own residual guards and only fires when the diff shows its evidence. These are
candidates; compilation, the frontend check and the object certificate decide. Unlike
`residual_evidence`, edits outside the function body are allowed, because the layout owners repair
the draft's own type declarations -- but never a preprocessor line.
"""
from __future__ import annotations

import re

from solver import rewrites

# Most specific evidence first: a layout owner reads offsets straight from the diff.
OWNERS = ("layout_rewrites", "per_object_layout_rewrites", "shared_layout_rewrites",
          "immediate_rewrites", "global_load_signedness_rewrites", "drop_mask_rewrites",
          "argswap_rewrites", "reloc_symbol_rewrites", "reloc_padding_rewrites",
          "frame_padding_rewrites", "stack_home_padding_rewrites", "pointer_table_deref_rewrites",
          "byte_pointer_offset_rewrites", "loop_shape_rewrites", "preincrement_lookup_rewrites",
          "narrow_increment_type_rewrites", "materialize_increment_input_rewrites", "prototype_rewrites")

_DIRECTIVE = re.compile(r"^[ \t]*#.*$", re.M)


class OwnerRaised(ValueError):
    """An owner raised on this source. Surfaced to the caller as a decline of that one family."""


def family(name: str) -> str:
    return "owner:" + name.removesuffix("_rewrites")


def candidates(name: str, source: str, diff: str):
    """(label, family, candidate) for one owner. Nothing without a diff: every owner is diff-keyed."""
    if not diff:
        return
    try:
        proposed = getattr(rewrites, name)(source, diff)
    except Exception as exc:                                         # noqa: BLE001
        raise OwnerRaised(f"{name}: {type(exc).__name__}: {exc}") from exc
    directives = _DIRECTIVE.findall(source)
    for rewrite in proposed:
        try:
            candidate = rewrite(source)
        except Exception as exc:                                     # noqa: BLE001
            raise OwnerRaised(f"{name}/{rewrite.label}: {type(exc).__name__}: {exc}") from exc
        if candidate == source or _DIRECTIVE.findall(candidate) != directives:
            continue
        yield f"{family(name)}:{rewrite.label}", family(name), candidate
