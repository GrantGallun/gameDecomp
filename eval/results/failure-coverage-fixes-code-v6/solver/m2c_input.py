"""Assembly-only m2c input sanitation; the oracle target is never modified."""
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from solver import project_headers


# O32 ABI spelling, matching m2c.arch_mips.MipsInstruction.o32abi_float_regs.
_EVEN_ALIASES = ("fv0", "fv1", "ft0", "ft1", "ft2", "ft3", "fa0", "fa1",
                 "ft4", "ft5", "fs0", "fs1", "fs2", "fs3", "fs4", "fs5")
_ALIASES = {name + suffix: f"f{2 * index + odd}"
            for index, name in enumerate(_EVEN_ALIASES)
            for suffix, odd in (("", 0), ("f", 1))}


def normalize_o32_registers(assembly: str) -> tuple[str, list[str]]:
    changed = set()
    lines = []
    for line in assembly.splitlines(keepends=True):
        # Keep comments, directives and labels intact. Only instruction operands
        # use $-prefixed registers; symbolic relocation names are not registers.
        match = re.match(r"(\s*(?:/\*.*?\*/\s*)?)([A-Za-z][\w.]*)\s+", line)
        if match and match.group(2) not in ("glabel", "endlabel", "nonmatching"):
            prefix, operands = line[:match.end()], line[match.end():]
            operands, separator, comment = operands.partition("#")
            def replace(m):
                old = m.group(1)
                new = _ALIASES.get(old)
                if new:
                    changed.add(f"${old}=${new}")
                    return "$" + new
                return m.group()
            operands = re.sub(r"\$([A-Za-z][A-Za-z0-9]*)\b", replace, operands)
            line = prefix + operands + separator + comment
        lines.append(line)
    return "".join(lines), sorted(changed)


def draft(repo: Path, target: Path, *, context_headers: tuple[str, ...] = (),
          extra_declarations: str = "", no_andor: bool = False, valid_syntax: bool = False,
          pointer_globals: tuple[tuple[str, str], ...] = ()):
    # Structured primitive declarations only: never accept arbitrary context C.
    if any(ctype not in {'s8','u8','s16','u16','s32','u32','void'} or
           not re.fullmatch(r'[A-Za-z_]\w*', name) for ctype,name in pointer_globals):
        raise ValueError('invalid generated primitive pointer declaration')
    if len({name for _,name in pointer_globals})!=len(pointer_globals):
        raise ValueError('duplicate generated pointer declaration')
    original = target.read_text()
    source, aliases = normalize_o32_registers(original)
    with tempfile.TemporaryDirectory(prefix=".m2c-input-", dir=target.parent) as temporary:
        path = Path(temporary) / "target.s"
        path.write_text(source)
        command = [str(repo / ".venv/bin/m2c"), "--target", "mips-ido-c"]
        if no_andor:
            command.append("--no-andor")
        if valid_syntax:
            command.append("--valid-syntax")
        context_meta = {}
        if context_headers or extra_declarations or pointer_globals:
            if not re.fullmatch(
                    r"(?:\s*extern\s+[A-Za-z_]\w*\s+[A-Za-z_]\w*\s*\[\s*\d*\s*\]\s*;)*\s*",
                    extra_declarations):
                raise ValueError("feedback context permits only generated extern array declarations")
            root = (repo / "include").resolve()
            for include in context_headers:
                if not re.fullmatch(r"[A-Za-z0-9_./-]+\.h", include):
                    raise ValueError("invalid context include spelling")
                header = (root / include).resolve()
                if not header.is_relative_to(root) or header.suffix != ".h" or not header.is_file():
                    raise ValueError("context must use existing project headers: " + include)
            wrapper = Path(temporary) / "headers.c"
            wrapper_source = "".join(f'#include "{inc}"\n' for inc in context_headers)
            wrapper_source += extra_declarations
            wrapper_source += ''.join(f'extern {ctype} *{name};\n' for ctype,name in pointer_globals)
            wrapper.write_text(wrapper_source)
            # Import the existing preprocessor function, not its CLI: the CLI
            # overwrites repo/ctx.c. The input contains no target function body.
            preprocessed = subprocess.run([
                sys.executable, "-c",
                "import runpy,sys; print(runpy.run_path(sys.argv[1])['import_c_file'](sys.argv[2]),end='')",
                str(repo / "tools/m2ctx.py"), str(wrapper)],
                cwd=repo, text=True, capture_output=True, timeout=120)
            if preprocessed.returncode:
                return preprocessed, {"context": "header preprocessing failed",
                                      "headers": list(context_headers)}
            context = Path(temporary) / "context.c"
            context.write_text(preprocessed.stdout)
            command += ["--context", str(context), "--no-cache"]
            context_meta = {"headers": list(context_headers),
                            "wrapper_sha256": hashlib.sha256(wrapper_source.encode()).hexdigest(),
                            "preprocessed_sha256": hashlib.sha256(preprocessed.stdout.encode()).hexdigest(),
                            "extra_declarations": extra_declarations,
                            "pointer_globals": list(pointer_globals)}
        result = subprocess.run([*command, str(path)], cwd=repo, text=True,
                                capture_output=True, timeout=120)
    return result, {"context": "headers plus assembly" if context_meta else "assembly only",
                    "no_andor": no_andor,
                    "valid_syntax": valid_syntax,
                    **context_meta, "o32_register_aliases": aliases,
                    "input_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "oracle_target_unchanged": target.read_text() == original}


def header_draft(repo: Path, function: str, target: Path, assembly: str, seed: str):
    headers = ["common.h", *project_headers.context_headers(repo, function, assembly),
               *project_headers.dependency_headers(repo, seed)]
    headers = tuple(dict.fromkeys(headers))
    return draft(repo, target, context_headers=headers)
