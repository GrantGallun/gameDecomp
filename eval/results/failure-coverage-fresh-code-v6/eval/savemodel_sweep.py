"""Which `totalsave` model actually predicts IDO's register choices?

uopt.py is explicit that half its model is quoted and half is guessed:

    KNOWN     the colour pool, nocs = ((n - 2) >> 2) + 2, descending save
    ASSUMED   totalsave -- modelled as occurrence weight, loop nesting 10x

and it says outright that if validation fails, the assumption is the first
suspect. Validation sits at 69.0% concordance over 1195 comparable pairs on
already-matched functions, which is not good enough to steer a source edit --
a prescription derived from it would be right about two times in three.

So rather than build on the guess, replace it with a measurement. Each
candidate below is a different reading of what IDO charges for a reference;
concordance against real IDO output picks between them. The corpus is the
matched set, where the target IS known-good compiler output.

This is `probe, don't assume` applied to the one part of the allocator model
that was never probed.

    python3 -m eval.savemodel_sweep
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from eval import matched as matched_mod
from solver import uopt

MEMORY = {"lw", "lh", "lhu", "lb", "lbu", "sw", "sh", "sb", "lwc1", "swc1"}


def _flat(_opcode: str, _depth: int) -> float:
    """Every reference counts once: save = n / nocs, nesting ignored."""
    return 1.0


def _loop10(_opcode: str, depth: int) -> float:
    """The current assumption: classic 10x per loop level."""
    return 10.0 ** depth


def _loop4(_opcode: str, depth: int) -> float:
    return 4.0 ** depth


def _loop8(_opcode: str, depth: int) -> float:
    return 8.0 ** depth


def _loop2(_opcode: str, depth: int) -> float:
    return 2.0 ** depth


def _mem_heavy(opcode: str, depth: int) -> float:
    """A reference that would become a memory access costs more to spill."""
    base = 2.0 if opcode in MEMORY else 1.0
    return base * (10.0 ** depth)


def _mem_only(opcode: str, depth: int) -> float:
    """Only memory references carry weight."""
    return (10.0 ** depth) if opcode in MEMORY else 0.0


MODELS = {
    "flat (n/nocs)": _flat,
    "loop 2x": _loop2,
    "loop 4x": _loop4,
    "loop 8x": _loop8,
    "loop 10x (current)": _loop10,
    "loop 10x, memory 2x": _mem_heavy,
    "memory refs only": _mem_only,
}


def paired_compare(repo: Path, names: list[str]) -> None:
    """Compare models on the SAME pairs, which the headline table cannot.

    `rank_agreement` only counts pairs whose predicted save DIFFERS, so a flat
    model excludes every tie and is scored on a smaller, easier subset -- 1371
    pairs against 1679. Comparing those accuracies directly flatters whichever
    model discriminates least. This restricts to web pairs that every model
    ranks strictly, so the denominators are identical and the numbers are
    actually about prediction quality.
    """
    labels = list(MODELS)
    hits = {k: 0 for k in labels}
    common = 0
    for name in names:
        tgt = repo / "nonmatchings" / name / "target_object_dump_normalized.s"
        if not tgt.exists():
            continue
        asm = tgt.read_text(errors="replace")
        per = {}
        for label, fn in MODELS.items():
            ws = uopt.webs(asm, weight_fn=fn)
            if len(ws) < 3:
                per = {}
                break
            uopt.mark_precolored(ws, asm)
            per[label] = {w.number: w for w in ws if not w.precolored}
        if not per:
            continue
        base = per[labels[0]]
        numbers = sorted(base)
        for i, na in enumerate(numbers):
            for nb in numbers[i + 1:]:
                ok = True
                for label in labels:
                    a, b = per[label].get(na), per[label].get(nb)
                    if (a is None or b is None or a.save == b.save
                            or a.register == b.register):
                        ok = False
                        break
                if not ok:
                    continue
                common += 1
                for label in labels:
                    a, b = per[label][na], per[label][nb]
                    early, late = ((a, b)
                                   if (a.save, -a.number) > (b.save, -b.number)
                                   else (b, a))
                    if (uopt.COLOR_INDEX[early.register]
                            < uopt.COLOR_INDEX[late.register]):
                        hits[label] += 1
    print("")
    print(f"PAIRED on the {common} pairs every model ranks strictly:")
    for label in sorted(labels, key=lambda k: -hits[k]):
        acc = 100.0 * hits[label] / common if common else 0.0
        print(f"  {label:<26}{hits[label]:>8}{acc:>9.1f}%")


def score_model(repo: Path, names: list[str], fn) -> tuple[int, int, int]:
    """(concordant, comparable, functions) over the matched corpus."""
    c_tot = n_tot = funcs = 0
    for name in names:
        tgt = repo / "nonmatchings" / name / "target_object_dump_normalized.s"
        if not tgt.exists():
            continue
        asm = tgt.read_text(errors="replace")
        ws = uopt.webs(asm, weight_fn=fn)
        if len(ws) < 3:
            continue
        uopt.mark_precolored(ws, asm)
        c, n = uopt.rank_agreement(ws)
        if not n:
            continue
        c_tot += c
        n_tot += n
        funcs += 1
    return c_tot, n_tot, funcs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / "decomp/kb-sbk1.sqlite"))
    ap.add_argument("--repo", default=str(Path.home() / "decomp/sbk1"))
    args = ap.parse_args()

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    names = sorted(matched_mod.already_matched(conn, repo))
    print(f"corpus: {len(names)} matched functions\n")
    print(f"{'totalsave model':<26}{'concordant':>12}{'pairs':>10}"
          f"{'accuracy':>10}{'funcs':>8}")
    print("-" * 66)

    rows = []
    for label, fn in MODELS.items():
        c, n, f = score_model(repo, names, fn)
        acc = 100.0 * c / n if n else 0.0
        rows.append((acc, label, c, n, f))
        print(f"{label:<26}{c:>12}{n:>10}{acc:>9.1f}%{f:>8}")

    rows.sort(reverse=True)
    print("-" * 66)
    best_acc, best_label = rows[0][0], rows[0][1]
    cur = next(r for r in rows if r[1] == "loop 10x (current)")
    print(f"best: {best_label} at {best_acc:.1f}%   "
          f"current: {cur[0]:.1f}%   delta {best_acc - cur[0]:+.1f} points")
    paired_compare(repo, names)
    if best_acc - cur[0] < 3.0:
        print("Inside the noise floor for this corpus: no model is clearly "
              "better, so the assumption stands unconfirmed either way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
