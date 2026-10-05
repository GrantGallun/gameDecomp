"""Build a realistic decompilation-repair-shaped prompt for throughput runs.

The prompt is real material: a target assembly listing from the repo's SBK1
nonmatching corpus, wrapped in the repair framing the solver uses. It is a
THROUGHPUT probe, not a scored eval case -- no held-out source is included and
no ground-truth answer is leaked; the response is not compiled here.

    python -m tools.lora_serve.build_bench_prompt \\
        --asm-file /home/grant/decomp/sbk1/nonmatchings/updateCourseSelectCourseList/target_object_dump.s \\
        --target-chars 12000 --out .cache/bench_prompt.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path

HEADER = """You are repairing a decompiled C function so that it compiles byte-for-byte \
to the target MIPS assembly below (IDO 5.3, -O2, N64).

Rules:
- Output ONLY the corrected C function inside a single ```c fenced block.
- Keep the existing signature, types and global names.
- Do not invent struct fields; if an offset is unknown, model it as bytes.
- No #include lines other than "common.h".

Target assembly (objdump, relocations resolved against the linked binary):

"""

FOOTER = """

Current best attempt: scores 71% of instructions identical; the residual is a
different branch shape around the loop guard and two missing stores near the
tail. Repair the C so the compiled object matches the target listing above.
Return the full corrected function.
"""


def build(asm_paths: list[Path], target_chars: int) -> str:
    listing = "".join(path.read_text(encoding="utf-8", errors="replace") + "\n"
                      for path in asm_paths)
    # Trim whole lines from the END of the listing so the prompt reads as a
    # truncated (but self-consistent) excerpt rather than a mangled instruction.
    budget = max(2000, target_chars - len(HEADER) - len(FOOTER))
    lines: list[str] = []
    used = 0
    for line in listing.splitlines(keepends=True):
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line)
    return HEADER + "".join(lines) + FOOTER


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asm-file", required=True, action="append",
                        help="target assembly listing; repeatable, concatenated in order")
    parser.add_argument("--target-chars", type=int, default=12000)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    prompt = build([Path(name) for name in args.asm_file], args.target_chars)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    print(f"wrote {out} ({len(prompt)} chars, {len(prompt.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
