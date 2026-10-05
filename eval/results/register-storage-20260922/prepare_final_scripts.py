"""Preserve the failed ordering revision and repeat its panel after repair."""
from prepare_next_scripts import copy

copy("freeze_next.py", "freeze_final.py", [
    ("code-v2", "code-v3"), ("freeze-v2.json", "freeze-v3.json"),
    ("mechanism frozen before fresh drafts; exposed SBK1 library functions, not sealed holdout",
     "ordering and namespace correction; repeat of exposed v2 panel, not fresh transfer")])
copy("measure_next.py", "measure_final.py", [
    ("freeze-v2.json", "freeze-v3.json"), ("paired-v2", "paired-v3"),
    ("storage-repair-v2:", "storage-repair-v3:")])
copy("audit_next.py", "audit_final.py", [
    ("paired-v2", "paired-v3"), ("measure_next.py", "measure_final.py")])
