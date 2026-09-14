"""Verify a MIPS function after relocation, including candidate data bytes.

Per-function object diffs can report false negatives when one object names a
literal ``.rodata+offset`` and the extracted target names ``D_80...`` at the
same address.  This tool does not ignore those differences.  It resolves them:

* link target and candidate text at the requested runtime address;
* place their local data sections at explicit runtime addresses;
* define address-named target symbols such as ``D_800E1A8C``;
* compare fully relocated text bytes; and
* compare every placed candidate data section with an explicit verified-ROM
  slice before it may report exactness.

The ordinary object oracle remains the fast primary gate.  This is a bounded
fallback for its relocation-only verdict, not a score relaxation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


SCHEMA_VERSION = 1
SECTION_RE = re.compile(r"^\.?[A-Za-z_][A-Za-z0-9_.-]*$")
ADDRESS_SYMBOL_RE = re.compile(r"^(?:D_|func_)([0-9A-Fa-f]{8})$")


def validate_exact_receipt(payload: dict[str, object]) -> None:
    """Reject incomplete or internally inconsistent promotion receipts."""
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get(
            "kind") != "mips_relocated_function_oracle_receipt":
        raise ValueError("unsupported relocated-oracle receipt")
    for gate in ("compiled", "oracle_tested", "exact"):
        if payload.get(gate) is not True:
            raise ValueError(f"relocated-oracle exact receipt failed {gate} gate")
    if payload.get("status") != "relocated_text_and_data_exact":
        raise ValueError("relocated-oracle receipt has non-exact status")
    relocated = payload.get("relocated_text")
    if not isinstance(relocated, dict) or relocated.get("equal") is not True:
        raise ValueError("relocated text is not exact")
    sections = payload.get("data_sections")
    if not isinstance(sections, list) or any(
            not isinstance(row, dict) or row.get("equal") is not True
            for row in sections):
        raise ValueError("candidate data sections are not all ROM-exact")
    if any(row.get("trailing_padding_bytes", 0)
           and row.get("trailing_padding_zero") is not True
           for row in sections):
        raise ValueError("candidate data section has nonzero trailing padding")
    if payload.get("unchecked_candidate_data_sections", []):
        raise ValueError("candidate has unchecked data sections")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_assignment(value: str) -> tuple[str, int]:
    try:
        section, raw_address = value.split("=", 1)
    except ValueError as exc:
        raise ValueError(f"expected SECTION=ADDRESS, got {value!r}") from exc
    if not SECTION_RE.fullmatch(section):
        raise ValueError(f"unsafe or invalid section name {section!r}")
    try:
        address = int(raw_address, 0)
    except ValueError as exc:
        raise ValueError(f"invalid address in {value!r}") from exc
    if not 0 <= address <= 0xFFFFFFFF:
        raise ValueError(f"address outside 32-bit range in {value!r}")
    return section, address


def parse_symbol_assignment(value: str) -> tuple[str, int]:
    symbol, address = parse_assignment(value)
    if symbol.startswith("."):
        raise ValueError("symbol definition cannot start with a dot")
    return symbol, address


def address_symbols(nm_output: str) -> dict[str, int]:
    result = {}
    for line in nm_output.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] != "U":
            continue
        match = ADDRESS_SYMBOL_RE.fullmatch(fields[1])
        if match:
            result[fields[1]] = int(match.group(1), 16)
    return result


def undefined_symbols(nm_output: str) -> set[str]:
    result = set()
    for line in nm_output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "U":
            result.add(fields[1])
    return result


def linker_script(text_address: int, sections: dict[str, int],
                  symbols: dict[str, int]) -> str:
    lines = ["SECTIONS", "{", f"  . = 0x{text_address:08X};",
             f"  .text 0x{text_address:08X} : SUBALIGN(1) {{ *(.text) }}"]
    for section, address in sorted(sections.items(), key=lambda item: item[1]):
        lines.extend([
            f"  . = 0x{address:08X};",
            # The requested address is authoritative evidence from the ROM.
            # Without SUBALIGN, ld silently rounds an input section with (for
            # example) 16-byte alignment from 0x...8BC to 0x...8C0.  The
            # receipt would then compare the right data bytes while relocating
            # text against the wrong address.
            f"  {section} 0x{address:08X} : SUBALIGN(1) {{ *({section}) }}",
        ])
    lines.extend([
        "  /DISCARD/ : { *(.MIPS.abiflags) *(.reginfo) *(.pdr) ",
        "                 *(.options) *(.gnu.attributes) }",
        "}",
    ])
    for symbol, address in sorted(symbols.items()):
        lines.append(f"{symbol} = 0x{address:08X};")
    return "\n".join(lines) + "\n"


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
    if result.returncode:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{(result.stdout + result.stderr)[-1600:]}")
    return result.stdout


def _extract_section(prefix: str, elf: Path, section: str, output: Path) -> None:
    _run([f"{prefix}objcopy", "-j", section, "-O", "binary",
          str(elf), str(output)])


def verify(
        *, target: Path, candidate: Path, text_address: int,
        target_sections: dict[str, int], candidate_sections: dict[str, int],
        rom: Path | None, rom_sections: dict[str, int],
        candidate_padding: dict[str, int] | None = None,
        symbols: dict[str, int] | None = None, tool_prefix: str = "mips-linux-gnu-",
        ) -> dict[str, object]:
    target = target.resolve()
    candidate = candidate.resolve()
    symbols = dict(symbols or {})
    candidate_padding = dict(candidate_padding or {})
    target_nm = _run([f"{tool_prefix}nm", "-u", str(target)])
    candidate_nm = _run([f"{tool_prefix}nm", "-u", str(candidate)])
    nm = target_nm + candidate_nm
    inferred = address_symbols(nm)
    conflicts = {name for name in inferred.keys() & symbols.keys()
                 if inferred[name] != symbols[name]}
    if conflicts:
        raise ValueError(f"explicit symbol addresses conflict: {sorted(conflicts)}")
    symbols = {**inferred, **symbols}
    # Shared semantic symbols need identical values, not their real ROM
    # addresses, to prove that the two relocated instruction streams match.
    # One-sided or misspelled symbols remain unresolved and are rejected.
    shared = undefined_symbols(target_nm) & undefined_symbols(candidate_nm)
    synthetic = {
        # MIPS J/JAL encode only 26 target bits and inherit the high nibble
        # from PC+4.  Keep synthetic calls in the target's 0x8 segment or ld
        # rejects an otherwise valid equality check as R_MIPS_26 overflow.
        name: 0x80010000 + (index * 0x100)
        for index, name in enumerate(sorted(shared - symbols.keys()))
    }
    symbols.update(synthetic)
    unknown = []
    for line in nm.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "U" and fields[1] not in symbols:
            unknown.append(fields[1])
    if unknown:
        raise ValueError(
            "unresolved non-address symbols require --symbol: "
            + ", ".join(sorted(set(unknown))))

    if set(rom_sections) - set(candidate_sections):
        raise ValueError("--rom-section names must also be candidate sections")
    if set(candidate_padding) - set(rom_sections):
        raise ValueError("--candidate-padding names must also be ROM sections")
    if any(value < 0 for value in candidate_padding.values()):
        raise ValueError("candidate padding cannot be negative")
    if rom_sections and rom is None:
        raise ValueError("--rom-section requires --rom")
    unchecked_data = sorted(set(candidate_sections) - set(rom_sections))

    with tempfile.TemporaryDirectory(prefix="relocated-oracle-") as raw_temp:
        temp = Path(raw_temp)
        target_script = temp / "target.ld"
        candidate_script = temp / "candidate.ld"
        target_script.write_text(linker_script(
            text_address, target_sections, symbols), encoding="ascii")
        candidate_script.write_text(linker_script(
            text_address, candidate_sections, symbols), encoding="ascii")
        target_elf = temp / "target.elf"
        candidate_elf = temp / "candidate.elf"
        _run([f"{tool_prefix}ld", "-T", str(target_script), "-o",
              str(target_elf), str(target)])
        _run([f"{tool_prefix}ld", "-T", str(candidate_script), "-o",
              str(candidate_elf), str(candidate)])
        target_text = temp / "target.text"
        candidate_text = temp / "candidate.text"
        _extract_section(tool_prefix, target_elf, ".text", target_text)
        _extract_section(tool_prefix, candidate_elf, ".text", candidate_text)
        target_text_bytes = target_text.read_bytes()
        candidate_text_bytes = candidate_text.read_bytes()
        text_equal = target_text_bytes == candidate_text_bytes
        mismatch_words = []
        if not text_equal:
            span = max(len(target_text_bytes), len(candidate_text_bytes))
            for offset in range(0, span, 4):
                expected = target_text_bytes[offset:offset + 4]
                actual = candidate_text_bytes[offset:offset + 4]
                if expected == actual:
                    continue
                mismatch_words.append({
                    "offset": f"0x{offset:X}",
                    "address": f"0x{(text_address + offset) & 0xFFFFFFFF:08X}",
                    "target": expected.hex(),
                    "candidate": actual.hex(),
                })
                if len(mismatch_words) == 16:
                    break

        section_results = []
        for section, rom_offset in sorted(rom_sections.items()):
            candidate_data = temp / f"candidate-{section.lstrip('.')}.bin"
            _extract_section(tool_prefix, candidate_elf, section, candidate_data)
            data = candidate_data.read_bytes()
            padding_bytes = candidate_padding.get(section, 0)
            if padding_bytes > len(data):
                raise ValueError(
                    f"candidate padding exceeds {section} section size")
            meaningful = data[:-padding_bytes] if padding_bytes else data
            padding = data[len(meaningful):]
            assert rom is not None
            with rom.open("rb") as rom_file:
                rom_file.seek(rom_offset)
                expected = rom_file.read(len(meaningful))
            equal = (meaningful == expected
                     and len(expected) == len(meaningful)
                     and all(byte == 0 for byte in padding))
            data_mismatches = []
            if meaningful != expected:
                for offset in range(0, max(len(meaningful), len(expected)), 4):
                    wanted = expected[offset:offset + 4]
                    actual = meaningful[offset:offset + 4]
                    if wanted == actual:
                        continue
                    data_mismatches.append({
                        "offset": f"0x{offset:X}",
                        "runtime_address": f"0x{candidate_sections[section] + offset:08X}",
                        "rom_offset": f"0x{rom_offset + offset:X}",
                        "rom": wanted.hex(),
                        "candidate": actual.hex(),
                    })
                    if len(data_mismatches) == 16:
                        break
            section_results.append({
                "section": section,
                "runtime_address": f"0x{candidate_sections[section]:08X}",
                "rom_offset": f"0x{rom_offset:X}",
                "bytes": len(meaningful),
                "candidate_section_bytes": len(data),
                "trailing_padding_bytes": padding_bytes,
                "trailing_padding_zero": all(byte == 0 for byte in padding),
                "candidate_sha256": hashlib.sha256(meaningful).hexdigest(),
                "rom_sha256": hashlib.sha256(expected).hexdigest(),
                "equal": equal,
                "first_mismatch_words": data_mismatches,
            })

        data_equal = all(row["equal"] for row in section_results)
        exact = text_equal and data_equal and not unchecked_data
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "mips_relocated_function_oracle_receipt",
            "role": "relocation-only fallback; ordinary object oracle remains primary",
            "target": {"path": str(target), "sha256": _sha256(target)},
            "candidate": {"path": str(candidate), "sha256": _sha256(candidate)},
            "text_address": f"0x{text_address:08X}",
            "inferred_address_symbols": dict(sorted(inferred.items())),
            "synthetic_shared_symbols": dict(sorted(synthetic.items())),
            "target_sections": {key: f"0x{value:08X}"
                                for key, value in target_sections.items()},
            "candidate_sections": {key: f"0x{value:08X}"
                                   for key, value in candidate_sections.items()},
            "relocated_text": {
                "bytes": len(candidate_text_bytes),
                "target_sha256": _sha256(target_text),
                "candidate_sha256": _sha256(candidate_text),
                "equal": text_equal,
                "first_mismatch_words": mismatch_words,
            },
            "data_sections": section_results,
            "unchecked_candidate_data_sections": unchecked_data,
            "compiled": True,
            "oracle_tested": True,
            "exact": exact,
            "status": ("relocated_text_and_data_exact" if exact else
                       "relocated_text_exact_data_unverified" if text_equal else
                       "relocated_text_mismatch"),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--text-address", required=True)
    parser.add_argument("--target-section", action="append", default=[])
    parser.add_argument("--candidate-section", action="append", default=[])
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument("--rom", type=Path)
    parser.add_argument(
        "--rom-section", action="append", default=[],
        help="compare candidate SECTION with ROM OFFSET: SECTION=OFFSET")
    parser.add_argument(
        "--candidate-padding", action="append", default=[],
        help="explicit trailing zero padding: SECTION=BYTES")
    parser.add_argument("--tool-prefix", default="mips-linux-gnu-")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        text_address = int(args.text_address, 0)
    except ValueError as exc:
        parser.error(f"invalid --text-address: {exc}")
    try:
        result = verify(
            target=args.target, candidate=args.candidate,
            text_address=text_address,
            target_sections=dict(parse_assignment(value)
                                 for value in args.target_section),
            candidate_sections=dict(parse_assignment(value)
                                    for value in args.candidate_section),
            rom=args.rom,
            rom_sections=dict(parse_assignment(value)
                              for value in args.rom_section),
            candidate_padding=dict(parse_assignment(value)
                                   for value in args.candidate_padding),
            symbols=dict(parse_symbol_assignment(value)
                         for value in args.symbol),
            tool_prefix=args.tool_prefix,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
