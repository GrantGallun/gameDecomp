"""Build reviewed/ copies of the two frozen files this amendment patches, with only the site-edit route added.

    solver/repair_queue.py        + site_edit_digest / site_edit_profile (block copied byte-for-byte from main,
                                    queue_block.py.txt), scheduled beside operand_repair, band -1
    eval/completion_campaign.py   + dispatch of `site_edits` profiles to eval/site_edit_repair.run

Every other line of each frozen file is untouched (test_amendment.py asserts it). Each anchor must occur
exactly once, or the build refuses.
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
    block = (HERE / 'queue_block.py.txt').read_text()
    anchor = 'def next_profile(node, model_calls, legacy_profiles, binary_revision=None):'
    text = replace_once(text, anchor, block + anchor, 'site_edit_profile definition')
    text = replace_once(text, '    operand = operand_profile(node)\n',
                        '    operand = operand_profile(node)\n    site = site_edit_profile(node)\n', 'profile call')
    text = replace_once(text, '    if operand is not None or binary_profile is not None:',
                        '    if operand is not None or site is not None or binary_profile is not None:', 'insert guard')
    text = replace_once(text, "[p for p in (operand, binary_profile) if p is not None]",
                        "[p for p in (site, operand, binary_profile) if p is not None]", 'insert list')
    text = replace_once(text, "        if profile.get('operand_repair'):\n",
                        "        if profile.get('site_edits'):\n"
                        "            # Measured 13/77 exact on small residuals (site-edits-20260929); one visit per revision.\n"
                        "            band = -1\n"
                        "        if profile.get('operand_repair'):\n", 'band')
    return text


def campaign(text: str) -> str:
    return replace_once(text, "    if profile.get('operand_repair'):\n        from eval import operand_repair\n",
                        "    if profile.get('site_edits'):\n"
                        "        from eval import site_edit_repair\n"
                        "        if profile.get('site_edit_revision') != repair_queue.site_edit_digest():\n"
                        "            raise ValueError('site-edit generator changed since dispatch')\n"
                        "        return site_edit_repair.run(repo=repo, db=db, function=function, node=node, out=out,\n"
                        "                                    budget=profile.get('site_edit_budget', 48))\n"
                        "    if profile.get('operand_repair'):\n        from eval import operand_repair\n", 'dispatch')


def main() -> None:
    for rel, patch in (('solver/repair_queue.py', queue), ('eval/completion_campaign.py', campaign)):
        out = OUT / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        # Patch on LF text, then restore the frozen file's own line ending so unchanged lines stay byte-identical.
        raw = (FROZEN / rel).read_bytes().decode('utf-8')
        eol = '\r\n' if '\r\n' in raw else '\n'
        patched = patch(raw.replace('\r\n', '\n'))
        out.write_bytes(patched.replace('\n', eol).encode('utf-8'))
        print(rel, 'written')


if __name__ == '__main__':
    main()
