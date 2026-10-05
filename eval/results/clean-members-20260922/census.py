"""Fresh, source-bound diagnostic census after the clean header repair."""
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.intake_probe import classify_residual
from solver import frontend_diagnostics, scalar_member_index

OUT = Path(__file__).resolve().parent
PARENT = ROOT / "eval/results/clean-conflicts-20260922"
paired = json.loads((PARENT / "paired.json").read_text())
prior = json.loads((PARENT / "census.json").read_text())
targets = {r["function"]: r["target"] for r in prior["rows"]}
REPO = Path.home() / "decomp/sbk1"
rows = []
classes = Counter()
messages = Counter()
base_errors = Counter()
base_states = defaultdict(set)
sole = defaultdict(list)
for entry in paired["rows"]:
    name = entry["function"]
    source = (PARENT / "replay" / name / "after.c").read_text()
    digest = hashlib.sha256(source.encode()).hexdigest()
    assert digest == entry["arms"]["after"]["source_sha256"]
    front = frontend_diagnostics.analyse(source, repo=REPO, target=targets[name])
    assert front["source_sha256"] == digest
    folder = OUT / "states" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "before.c").write_text(source)
    (folder / "frontend.json").write_text(json.dumps(front, indent=2) + "\n")
    counts = Counter(classify_residual(e["what"]) for e in front["errors"])
    classes.update(counts.keys())
    if len(counts) == 1:
        sole[next(iter(counts))].append(name)
    for error in front["errors"]:
        message = error["what"]
        messages[re.sub(r"'[^']*'", "'<value>'", message)] += 1
        match = re.match(r"member reference base type '([^']+)'", message)
        if match:
            base_errors[match[1]] += 1
            base_states[match[1]].add(name)
    # The structured list is complete even when stored diagnostic text is capped.
    diagnostics = "\n".join(f"candidate.c:{e['line']}:{e['column']}: error: {e['what']}" for e in front["errors"])
    _, decisions = scalar_member_index.rewrite(source, diagnostics)
    declines = Counter(d.get("declined") for d in decisions if d.get("declined"))
    rows.append({"function": name, "target": targets[name], "source_sha256": digest,
                 "compiled": entry["arms"]["after"]["compiled"], "exact": entry["arms"]["after"]["exact"],
                 "frontend": front["status"], "errors": front["error_count"], "classes": dict(counts),
                 "scalar_declines": dict(declines),
                 "fresh_matches_previous": front["error_count"] == entry["arms"]["after"]["errors"]})
    if len(rows) % 50 == 0:
        print(f"Rechecked {len(rows)}/200", flush=True)
summary = {"states": len(rows), "classes": dict(classes.most_common()),
           "member_base_types": {key: {"states": len(base_states[key]), "errors": count} for key, count in base_errors.most_common()},
           "sole_class": dict(sole), "messages": dict(messages.most_common(20)), "rows": rows}
(OUT / "census.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
