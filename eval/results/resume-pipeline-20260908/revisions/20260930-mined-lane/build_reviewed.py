"""Build reviewed/solver/repair_queue.py: the frozen file plus ONLY the mined-lane changes from main.

Main's repair_queue.py also carries unrelated investigation-policy / capability-task edits (they are not part of this
amendment and are NOT deployed). The two hunks shipped are:

  * site_edit_digest covers the shape and mined lane sources, so a changed generator or rule table is a new visit
  * the site-edit budget 48 -> 72 (depth 3 x per_step 24; at 48 the third level was unreachable)

Each anchor must occur exactly once, or the build refuses. test_amendment.py asserts every other line is untouched.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
FROZEN = HERE.parents[1] / 'code'
OUT = HERE / 'reviewed'


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f'anchor for {what} occurs {text.count(old)} times')
    return text.replace(old, new, 1)


def queue(text: str) -> str:
    text = replace_once(
        text, "'solver/diffrepair.py', 'solver/c89.py', 'solver/code_shapes.py', 'solver/workspace.py'):",
        "'solver/diffrepair.py', 'solver/c89.py', 'solver/code_shapes.py', 'solver/workspace.py',\n"
        "                     # the shape and mined lanes (2026-09-29): a changed generator or rule table is a new visit\n"
        "                     'solver/branch_shape.py', 'solver/unaligned_copy.py', 'solver/temp_copyback.py',\n"
        "                     'solver/counted_loop.py', 'solver/residual_classes.py', 'solver/rewrite_library.py',\n"
        "                     'solver/rule_miner.py', 'patterns/equivalences.py', 'patterns/mined_rules.json',\n"
        "                     'solver/term_rewrite.py'):", 'digest sources')
    return replace_once(text, "'site_edits': True, 'site_edit_budget': 48,", "'site_edits': True, 'site_edit_budget': 72,",
                        'budget')


def signals(text: str) -> str:
    """solver/signals.py: add `distances` (+ ALLOCATABLE) byte-for-byte from main, used by residual_classes.counts.

    Main's signals.py also changes analyse() (branch shifts stop counting as structural faults). That changes the
    fault counts every node is gated on campaign-wide, so it is NOT deployed here."""
    main = (HERE.parents[4] / 'solver/signals.py').read_bytes().decode('utf-8').replace('\r\n', '\n')
    anchor = 'def analyse(diff: str, score: float = 0.0, exact: bool = False,'
    block = main[main.index('ALLOCATABLE = re.compile('):main.index(anchor)]
    return replace_once(text, anchor, block + anchor, 'signals.distances')


def main() -> None:
    for rel, patch in (('solver/repair_queue.py', queue), ('solver/signals.py', signals)):
        out = OUT / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = (FROZEN / rel).read_bytes().decode('utf-8')
        eol = '\r\n' if '\r\n' in raw else '\n'
        out.write_bytes(patch(raw.replace('\r\n', '\n')).replace('\n', eol).encode('utf-8'))
        print(rel, 'written')


if __name__ == '__main__':
    main()
