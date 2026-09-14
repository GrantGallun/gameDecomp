"""Apply only audited prompt-budget hunks to a root-owned frozen staging tree."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]


def apply_frozen(stage_dir):
    stage_dir = Path(stage_dir).resolve()
    live = (ROOT / 'eval/results/resume-pipeline-20260908/code').resolve()
    if stage_dir == live or not (stage_dir / 'solver/modelrepair.py').is_file():
        raise ValueError('requires an existing separate frozen staging tree')
    path = stage_dir / 'solver/modelrepair.py'
    text = path.read_text()
    hunks = [
        ("                    prompt += '\\nSEMANTIC REPAIR OBJECTIVE (same frozen target-led panel):\\n'+json.dumps(parent.semantic)",
         "                    from solver.prompt_budget import semantic_summary\n"
         "                    prompt += '\\nSEMANTIC REPAIR OBJECTIVE (same frozen target-led panel):\\n'+json.dumps(semantic_summary(parent.semantic))"),
        ('                            sampling={"temperature": temperature,\n                                      "seed": call_seed,\n',
         '                            sampling={"temperature": temperature,\n                                      "seed": call_seed,\n                                      "context_budget": getattr(exc, \'context_budget\', None),\n'),
        ('                                  "done_reason": meta.get("done_reason"),\n',
         '                                  "done_reason": meta.get("done_reason"),\n                                  "context_budget": meta.get(\'_context_budget\'),\n'),
    ]
    for old, new in hunks:
        if text.count(old) != 1:
            raise ValueError('frozen modelrepair hunk is missing or ambiguous; inspect staging input')
        text = text.replace(old, new, 1)
    path.write_text(text)
    for name in ('prompt_budget.py', 'llm.py'):
        shutil.copy2(ROOT / 'solver' / name, stage_dir / 'solver' / name)
    return [str(stage_dir / 'solver' / name) for name in ('modelrepair.py', 'prompt_budget.py', 'llm.py')]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage_dir', type=Path)
    print('\n'.join(apply_frozen(parser.parse_args().stage_dir)))
