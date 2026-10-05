"""Apply only this feature's edits to the pinned campaign, never copy main wholesale."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / 'eval/results/resume-pipeline-20260908/revisions/20261004-compiler-localization'
FROZEN = HERE.parents[1] / 'code'
HERE.mkdir(parents=True, exist_ok=True)
REVIEWED = HERE / 'reviewed'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('non-unique or absent amendment anchor: ' + repr(old))
    return text.replace(old, new)


def block(text, a, b):
    return text[text.index(a):text.index(b, text.index(a))]


main = (ROOT / 'solver/modelrepair.py').read_text()
parent_block = block(main, '            localization_packet = None\n', '            pending_completion = ""\n')
profile_block = block((ROOT / 'eval/completion_campaign.py').read_text(),
                      '    {"name": "localized_patch"', '\n)\n')
profile_guard = "        if profile.get('compiler_localization') and node.get('residual', {}).get('compiled') is not True:\n            continue\n"
queue_guard = "    profiles = [p for p in profiles if not p.get('compiler_localization') or (\n        phase == Lane.BYTE and (node.get('residual') or {}).get('compiled') is True)]\n"
render_block = "    if localization:\n        from solver import compiler_localization as localization_adapter\n        diagnosis_block += localization_adapter.render(localization, source, attempt.diff or '')\n"
edits = {
    'solver/modelrepair.py': [
        ('rejected: list[str] | None = None, type_transaction: bool = False) -> str:', 'rejected: list[str] | None = None, type_transaction: bool = False, function: str | None = None, localization=None) -> str:'),
        ('    packet = packet or residual.build(attempt, target_asm=asm)\n', '    packet = packet or residual.build(attempt, target_asm=asm)\n' + render_block),
        ('early_patch_guidance: bool = False) -> Result:', 'early_patch_guidance: bool = False, compiler_localization: bool = False) -> Result:'),
        ('        "early_patch_guidance": early_patch_guidance,\n', '        "early_patch_guidance": early_patch_guidance,\n        "compiler_localization": compiler_localization,\n'),
        ('    stalled_depths = 0\n    failed_plans', '    stalled_depths = 0\n    localization_cache = {}\n    failed_plans'),
        ('        for parent_index, parent in enumerate(parents):\n', '        for parent_index, parent in enumerate(parents):\n' + parent_block),
        ('history=parent.labels, rejected=rejected, type_transaction=type_transaction)',
         'history=parent.labels, rejected=rejected, type_transaction=type_transaction, localization=localization_packet)'),
    ],
    'eval/agentrepair.py': [
        ('structural_rounds: int = 0, stack_rounds: int = 0) -> dict:', 'structural_rounds: int = 0, stack_rounds: int = 0, compiler_localization: bool = False) -> dict:'),
        ('        "provider": provider.provider_id,\n', '        "provider": provider.provider_id,\n        "compiler_localization": compiler_localization,\n'),
        ('resilient=resilient,semantic_evaluator=panel, exhaust_budget=exhaust_budget)',
         'resilient=resilient,semantic_evaluator=panel, exhaust_budget=exhaust_budget, compiler_localization=compiler_localization)'),
    ],
    'eval/completion_campaign.py': [
        ('              "Use target return dataflow rather than inventing return values to silence diagnostics."},\n)',
         '              "Use target return dataflow rather than inventing return values to silence diagnostics."},\n' + profile_block + '\n)'),
        ('    for profile in profiles:\n', '    for profile in profiles:\n' + profile_guard),
        ("        type_transaction=profile.get('type_transaction', False),\n", "        type_transaction=profile.get('type_transaction', False),\n        compiler_localization=profile.get('compiler_localization', False),\n"),
    ],
    'solver/repair_queue.py': [
        ('    for profile in profiles:\n', queue_guard + '    for profile in profiles:\n'),
    ],
}
new = ('solver/compiler_localization.py', 'solver/line_map.py')
reviewed = {}
for rel in (*edits, *new):
    old = FROZEN / rel
    if rel in new:
        if old.exists():
            raise RuntimeError('new module already frozen: ' + rel)
        text = (ROOT / rel).read_text()
    else:
        text = old.read_text()
        for before, after in edits[rel]:
            text = replace_once(text, before, after)
    ast.parse(text)
    out = REVIEWED / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, newline='\n')
    reviewed[rel] = [hashlib.sha256(old.read_bytes()).hexdigest() if old.exists() else None,
                     hashlib.sha256(out.read_bytes()).hexdigest()]
(HERE / 'payload-hashes.json').write_text(json.dumps(reviewed, indent=2))
print(json.dumps(reviewed, indent=2))
