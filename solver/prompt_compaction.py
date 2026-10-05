"""Lossless compaction of model prompts, applied once before every generation (2026-09-15).

Measured on the 600 most recent prompts for non-compiling parents (eval/results/ninety-census-20260914/
compression_savings.py): splat's per-instruction `/* ROM-offset VRAM word */` annotations and column
padding cost a median 8 KB per prompt (about 60% of the assembly), and indent=2 JSON another 5 KB.
Removing both shrinks prompts 27% and lets 448 of 600 fit the 32K window instead of 314.

Only two shapes are rewritten, both without information loss for the model:
  * a line carrying a splat annotation loses the annotation and its padding (mnemonic and operands kept,
    labels untouched). C comments never match: the pattern requires three hex fields, the last two of
    eight digits;
  * a top-level pretty-printed JSON object that parses is re-encoded with compact separators.
"""
from __future__ import annotations

import json
import re

ANNOTATION = re.compile(r"/\*\s*[0-9A-Fa-f]{1,8}\s+[0-9A-Fa-f]{8}\s+[0-9A-Fa-f]{8}\s*\*/")
PRETTY_JSON = re.compile(r"(?ms)^(\{\n.*?\n\})$")


def compact_assembly(text: str) -> str:
    out = []
    for line in text.split("\n"):
        if ANNOTATION.search(line):
            body = ANNOTATION.sub("", line)
            line = "  " + re.sub(r"\s+", " ", body).strip()
        out.append(line)
    return "\n".join(out)


def compact_json(text: str) -> str:
    def dense(match):
        try:
            return json.dumps(json.loads(match.group(1)), separators=(",", ":"), ensure_ascii=False)
        except ValueError:
            return match.group(1)
    return PRETTY_JSON.sub(dense, text)


def compact(prompt: str) -> str:
    return compact_json(compact_assembly(prompt))
