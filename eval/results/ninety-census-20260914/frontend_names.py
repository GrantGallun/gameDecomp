"""Which identifiers/types the non-compiling frontend errors name (reads frontend_errors.json)."""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
rows = json.loads((HERE / "frontend_errors.json").read_text())["rows"]
named = defaultdict(Counter)
for row in rows:
    for e in row["errors"]:
        m = e["message"]
        for pattern, key in ((r"use of undeclared identifier '([^']+)'", "undeclared"),
                             (r"unknown type name '([^']+)'", "unknown_type"),
                             (r"member reference base type '([^']+)'", "member_on_nonstruct"),
                             (r"no member named '([^']+)' in '([^']+)'", "no_member"),
                             (r"implicit declaration of function '([^']+)'", "implicit_function"),
                             (r"incomplete definition of type '([^']+)'", "incomplete")):
            found = re.search(pattern, m)
            if found:
                value = found.group(1)
                if key == "undeclared":
                    value = ("m2c_local:" + re.sub(r"\d.*", "", value) if re.match(r"(var_|temp_|phi_|sp[0-9A-Fa-f]+$|arg\d|saved_reg_)", value)
                             else "D_/func_" if re.match(r"(D_|func_|B_|jtbl_)", value) else value)
                if key == "no_member":
                    value = f"{found.group(1)} in {found.group(2)}"
                named[key][(value, row["function"])] += 1
for key, counter in named.items():
    by_value = Counter()
    nodes = defaultdict(set)
    for (value, function), count in counter.items():
        by_value[value] += count
        nodes[value].add(function)
    print(f"\n== {key}: {sum(by_value.values())} errors, {len({f for _, f in counter})} nodes")
    for value, count in by_value.most_common(25):
        print(f"  {count:4} {len(nodes[value]):3} nodes  {value[:60]}  e.g. {sorted(nodes[value])[0]}")
