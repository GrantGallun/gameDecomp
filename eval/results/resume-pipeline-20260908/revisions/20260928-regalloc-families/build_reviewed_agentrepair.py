"""Build the reviewed agentrepair.py: the FROZEN file with only `_regalloc_search` replaced by main's.

Main's agentrepair also carries unreviewed investigation-policy work in other functions; only the register
search call site belongs to this amendment (certificate rechecks, 2% audit, coalesce=True). Writes
reviewed/eval/agentrepair.py and agentrepair.diff (frozen -> reviewed) for review.
"""
import difflib
import hashlib
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = HERE.parents[4]
FROZEN = HERE.parents[1] / "code"


def function_span(text: str, name: str) -> tuple[int, int]:
    start = re.search(rf"^def {name}\(", text, re.M).start()
    nxt = re.search(r"^def \w+\(", text[start + 1:], re.M)
    return start, (start + 1 + nxt.start()) if nxt else len(text)


frozen = (FROZEN / "eval/agentrepair.py").read_text(encoding="utf-8")
main = (MAIN / "eval/agentrepair.py").read_text(encoding="utf-8")
fs, fe = function_span(frozen, "_regalloc_search")
ms, me = function_span(main, "_regalloc_search")
reviewed = frozen[:fs] + main[ms:me] + frozen[fe:]
out = HERE / "reviewed/eval/agentrepair.py"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(reviewed, encoding="utf-8", newline="")
diff = "".join(difflib.unified_diff(frozen.splitlines(True), reviewed.splitlines(True),
                                    "frozen/eval/agentrepair.py", "reviewed/eval/agentrepair.py"))
(HERE / "agentrepair.diff").write_text(diff, encoding="utf-8", newline="")
print("frozen", hashlib.sha256(frozen.encode()).hexdigest())
print("reviewed", hashlib.sha256(reviewed.encode()).hexdigest())
print("changed lines", sum(1 for l in diff.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))))
