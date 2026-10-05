"""Derived from frontend diagnostics: repair type-validity errors on candidates whose bytes already match.

Evidence is the diagnostic itself (line, column, and the types it names). Rules:
  "incompatible (integer to pointer|pointer to integer) conversion assigning to 'A' from 'B'"
      -> cast the right-hand side of that assignment to A (same-width int/pointer casts emit no code)
  "a function declaration without a prototype is deprecated"
      -> `()` at that line becomes `(void)`
Every candidate still goes through compile, frontend and the object certificate.
"""
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DIAG = re.compile(r"^candidate\.c:(\d+):(\d+): error: (.*)$", re.M)
ASSIGN = re.compile(r"incompatible (?:integer to pointer|pointer to integer) conversion assigning to '([^']+)'")


def fix(source, diagnostics):
    lines = source.split("\n")
    changed = False
    # The frontend compiles a wrapper: user source starts after a `#line 1 "candidate.c"` directive, so the
    # reported lines are user-source lines.
    for m in sorted(DIAG.finditer(diagnostics), key=lambda m: -int(m.group(1))):
        n, message = int(m.group(1)), m.group(3)
        if not 1 <= n <= len(lines):
            continue
        text = lines[n - 1]
        a = ASSIGN.search(message)
        if a:
            ctype = a.group(1)
            eq = re.search(r"(?<![=!<>+\-*/%&|^])=(?!=)\s*(.+?);", text)
            if eq and not eq.group(1).startswith(f"({ctype})"):
                lines[n - 1] = text[:eq.start(1)] + f"({ctype})({eq.group(1)})" + text[eq.end(1):]
                changed = True
        elif "without a prototype" in message:
            new = re.sub(r"\(\s*\)", "(void)", text, count=1)
            if new != text:
                lines[n - 1] = new
                changed = True
    return "\n".join(lines) if changed else None


def main():
    hits = json.loads((HERE / "bytes-exact-frontend-rejected.json").read_text())
    probes = []
    for function, nodes in hits.items():
        if function == "releaseRelocatableHeapBlockMetadata":
            continue
        for h in nodes[:3]:
            world = json.loads(Path(h["world"]).read_text())["world"]
            node = next(n for n in world["nodes"] if n["id"] == h["id"])
            diagnostics = (node["verdict"].get("frontend") or {}).get("diagnostics") or ""
            new = fix(node["source"], diagnostics)
            if new and all(p["source"] != new for p in probes):
                probes.append({"function": function, "label": f"forward:frontend-type:{h['id']}", "source": new})
    (HERE / "probes-frontend2.json").write_text(json.dumps(probes, indent=1))
    print(len(probes), "candidates:", sorted({p["function"] for p in probes}))


if __name__ == "__main__":
    main()
