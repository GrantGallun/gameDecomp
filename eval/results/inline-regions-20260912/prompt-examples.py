"""Render the actual hint on retained target assembly; no C or live reads."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.inline_regions import load_saved
from solver import cfg, inline_regions

folder = Path(__file__).resolve().parent / 'current-v4'
inputs = load_saved(folder / 'assemblies.json')
ranked = sorted(((len(cfg.parse_assembly(text)[0]), name, text)
                 for name, text in inputs['assemblies'].items()), reverse=True)
examples = []
for count, name, assembly in ranked:
    if count < 128:
        break
    prompt = inline_regions.prompt(assembly)
    if not prompt:
        continue
    report = inline_regions.analyse({name: assembly})
    examples.append({'function': name, 'instruction_count': count,
                     'assembly_sha256': hashlib.sha256(assembly.encode()).hexdigest(),
                     'pattern_count': report['pattern_count'], 'prompt_chars': len(prompt),
                     'prompt': prompt, 'patterns': report['patterns'][:3]})
    if len(examples) == 3:
        break
result = {'checkpoint': inputs['checkpoint'], 'selection': 'Largest instruction-count functions with nonempty within-caller hints',
          'scope': 'Prompt component only; no C or model request; no correctness claim',
          'examples': examples}
(folder / 'prompt-examples.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps([{key: row[key] for key in ('function', 'instruction_count', 'pattern_count', 'prompt_chars')}
                  for row in examples]))
