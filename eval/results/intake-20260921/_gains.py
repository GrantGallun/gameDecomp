"""Why did three states newly compile? Attribute the difference to an action, not to a hunch."""
import json
from pathlib import Path

BASE = Path("eval/results/intake-20260921")
new = {r["function"]: r for r in json.loads((BASE / "wide-intake-acceptance.json").read_text())["rows"]}
old = {r["function"]: r for r in json.loads((BASE / "wide-intake-orfix.json").read_text())["rows"]}

gained = [n for n in new if not old[n]["sequence"]["compiled"] and new[n]["sequence"]["compiled"]]
for name in gained:
    before, after = old[name], new[name]
    fired_before = {k.split(".")[-1]: v.get("changed") for k, v in before["actions"].items()}
    fired_after = {k.split(".")[-1]: v.get("changed") for k, v in after["actions"].items()}
    changed_steps = [k for k in fired_after if fired_before.get(k) != fired_after.get(k)]
    compiled_by = [k.split(".")[-1] for k, v in after["actions"].items() if v.get("compiled")]
    print(f"\n{name}  tier={after.get('tier')} size={after.get('size')}")
    print(f"  steps whose firing changed : {changed_steps}")
    print(f"  per-action compiled        : {compiled_by}")
    print(f"  frontend on final          : {after['sequence'].get('frontend')} "
          f"errors={after['sequence'].get('frontend_errors')} "
          f"classes={after['sequence'].get('frontend_classes')}")
    print(f"  sequence before/after      : {before['sequence']['score']} -> {after['sequence']['score']}")

# the opposite direction, for completeness
lost = [n for n in new if old[n]["sequence"]["compiled"] and not new[n]["sequence"]["compiled"]]
print(f"\nlost: {lost}")
