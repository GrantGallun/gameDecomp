"""Which main-tree solver files an amendment must stage: the import closure (within solver/) of the machinery behind
the 2026-09-25 gains, restricted to files that differ from the campaign's frozen code. Read-only."""
import ast, hashlib, json, sys
from pathlib import Path
MAIN = Path("/mnt/c/Code/gameDecomp")
FROZEN = MAIN / "eval/results/resume-pipeline-20260908/code"
ROOTS = ["regalloc_search", "regalloc_mutations", "byte_certificate", "workspace", "rodata_symbol"]


def imports(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "solver":
                out |= {a.name for a in node.names}
            elif node.module.startswith("solver."):
                out.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            out |= {a.name.split(".")[1] for a in node.names if a.name.startswith("solver.")}
    return out


seen, todo = set(), list(ROOTS)
while todo:
    m = todo.pop()
    if m in seen or not (MAIN / "solver" / f"{m}.py").exists():
        continue
    seen.add(m)
    todo += list(imports(MAIN / "solver" / f"{m}.py"))
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
changed = sorted(m for m in seen if sha(MAIN / "solver" / f"{m}.py") != sha(FROZEN / "solver" / f"{m}.py"))
new = [m for m in changed if not (FROZEN / "solver" / f"{m}.py").exists()]
# frozen eval modules that import any changed solver module (compatibility surface)
users = {}
for p in (FROZEN / "eval").glob("*.py"):
    hit = imports(p) & set(changed)
    if hit:
        users[p.name] = sorted(hit)
out = {"closure": sorted(seen), "changed": changed, "new": new, "frozen_eval_users": users}
Path(__file__).with_name("closure.json").write_text(json.dumps(out, indent=1))
print(len(seen), "in closure;", len(changed), "differ;", len(new), "new;", len(users), "frozen eval modules import changed ones")
print("changed:", changed)
