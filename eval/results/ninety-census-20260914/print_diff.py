"""Print the stored oracle diff text of a diffs/ entry: python3 print_diff.py FUNCTION"""
import json
import sys
from pathlib import Path

print(json.loads((Path(__file__).resolve().parent / "diffs" / f"{sys.argv[1]}.json").read_text())["diff"][:3000])
