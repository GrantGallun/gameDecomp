"""Classify a mismatch with n64-decomp-workbench and fetch the matching levers.

Score-band triage was a crude stand-in. The workbench -- already a dependency
of the reference repo, and used by its author for exactly this -- classifies a
mismatch into a VERDICT and names the PLAYBOOK of levers that address it:

    allocation / allocation-mismatch      -> pool-position
    constant / constant-mismatch          -> constant-audit
    frame-layout / frame-layout-mismatch  -> stack-frame-recovery
    schedule / schedule-mismatch          -> g0-schedule-probe
    structure / structure-mismatch        -> structure-buckets
    register-permutation                  -> forced-color-oracle
    phase-shift                           -> temp-fifo-phase
    commutative-order                     -> ast-shape
    words-identical / *relocation*        -> relocation-only

That last row is the failure mode found the hard way here -- zero instruction
differences and still not byte-exact -- and it is a KNOWN verdict with its own
playbook. Score cannot see it at all: such a candidate scores ~100% and a
score-band router sends it to the permuter, which permutes register allocation
and will never touch a symbol reference.

The verdict also carries facts worth stating outright: differing frame sizes,
and relocation sites naming different symbols in each object.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VERDICT_RE = re.compile(r"verdict=(\S+)")
PLAYBOOK_RE = re.compile(r"playbook=(\S+)")
FRAME_RE = re.compile(r"target_frame_size=(\S+)\s+candidate_frame_size=(\S+)")
RELOC_SYM_RE = re.compile(r"symbol=(\w+)")
INSN_COUNT_RE = re.compile(
    r"true instruction count differs: target=(\d+) candidate=(\d+)")

# Verdicts the workbench maps to a playbook, plus the relocation family it
# reports in prose rather than as a playbook token.
RELOC_VERDICTS = {"words-identical", "relocation-layout-mismatch",
                  "unknown-relocation"}


@dataclass
class Diagnosis:
    verdict: str = ""
    playbook: str = ""
    target_frame: str = ""
    candidate_frame: str = ""
    target_insns: int = 0
    candidate_insns: int = 0
    reloc_symbols: list = field(default_factory=list)
    raw: str = ""

    @property
    def frame_mismatch(self) -> bool:
        return (self.target_frame and self.candidate_frame
                and self.target_frame != self.candidate_frame)


def run(repo: Path, target_o: Path, candidate_o: Path,
        timeout: int = 180) -> Diagnosis | None:
    cmd = (f". {repo}/.venv/bin/activate && python3 -m decomp_workbench "
           f"diagnose {target_o} {candidate_o}")
    try:
        proc = subprocess.run(["bash", "-lc", cmd], cwd=candidate_o.parent,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None

    out = proc.stdout + proc.stderr
    if "verdict" not in out:
        return None

    d = Diagnosis(raw=out)
    m = VERDICT_RE.search(out)
    if m:
        d.verdict = m.group(1)
    m = PLAYBOOK_RE.search(out)
    if m:
        d.playbook = m.group(1)
    m = FRAME_RE.search(out)
    if m:
        d.target_frame, d.candidate_frame = m.group(1), m.group(2)
    m = INSN_COUNT_RE.search(out)
    if m:
        d.target_insns, d.candidate_insns = int(m.group(1)), int(m.group(2))

    if "relocation target differences" in out:
        tail = out[out.find("relocation target differences"):]
        d.reloc_symbols = sorted(set(RELOC_SYM_RE.findall(tail)))[:6]
    return d


def levers(repo: Path, playbook: str, max_chars: int = 2200,
           timeout: int = 60) -> str:
    """The field-guide levers for one playbook, trimmed for a prompt."""
    if not playbook:
        return ""
    cmd = (f". {repo}/.venv/bin/activate && python3 -m decomp_workbench "
           f"guide {playbook}")
    try:
        proc = subprocess.run(["bash", "-lc", cmd], cwd=repo,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ""
    text = proc.stdout.strip()
    if not text:
        return ""
    # Drop the header lines; keep the numbered levers themselves.
    body = text.split("-" * 40, 1)[-1].strip()
    return body[:max_chars]


# Above this much target assembly, append only the short verdict FACTS and
# drop the general playbook prose. Measured on the dev set: tiny/small targets
# run under ~3.1k chars, medium 3-8k, large 7.7-17.8k. The playbook body is
# ~2.2k of general guidance, while the facts (frame mismatch, differing
# relocation symbols, instruction-count delta) are ~200 chars and specific.
# Adding the prose is what iteration 4 did, and large-tier mean fell
# 33.3 -> 21.6 in that run. This gates it so the hypothesis can be tested.
LARGE_ASM_CHARS = 6000


def prompt_block(repo: Path, d: Diagnosis, asm_len: int = 0) -> str:
    """Turn a diagnosis into guidance the model can act on.

    `asm_len` gates the verbose half: specific facts always help, general
    prose may crowd a prompt that is already long.
    """
    if d is None or not d.verdict:
        return ""

    out = [f"\nMISMATCH DIAGNOSIS (n64-decomp-workbench): {d.verdict}"]

    if d.verdict in RELOC_VERDICTS or (d.reloc_symbols and "words-identical" in d.verdict):
        out.append("  The INSTRUCTIONS are right; the RELOCATIONS are not. Stop "
                   "changing the code. A symbol is referenced by the wrong name "
                   "or through a different expression.")
    if d.reloc_symbols:
        out.append(f"  Relocation sites disagree on: {', '.join(d.reloc_symbols)}")
        out.append("  Check the exact spelling and declared type of these "
                   "symbols -- `arr` vs `&arr[0]` emits the same instruction "
                   "with a different relocation.")

    if d.frame_mismatch:
        out.append(f"  FRAME SIZE differs: target={d.target_frame}, "
                   f"yours={d.candidate_frame}. Fix this first -- every stack "
                   f"offset shifts with it. It cannot be permuted away.")

    if d.target_insns and d.candidate_insns and d.target_insns != d.candidate_insns:
        delta = d.candidate_insns - d.target_insns
        out.append(f"  INSTRUCTION COUNT differs by {delta:+d} "
                   f"(target {d.target_insns}, yours {d.candidate_insns}). A "
                   f"count difference is structural, not register allocation.")

    if asm_len and asm_len > LARGE_ASM_CHARS:
        out.append(f"  (playbook `{d.playbook}` withheld: large target, "
                   f"keeping the prompt tight)")
    else:
        guide = levers(repo, d.playbook)
        if guide:
            out.append(f"\n  Field-guide playbook `{d.playbook}` for this verdict:")
            out.append(guide)

    return "\n".join(out) + "\n"
