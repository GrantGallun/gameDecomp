"""Fetch public decomp repositories and turn their matched C into (assembly, C) training pairs.

No ROM is needed. A function a decomp project marks as matched compiles, under that project's own
recipe, to exactly the bytes in the game -- that is what "matched" means -- so compiling its C with
the recorded flags yields the target assembly. Nothing here is verified against a ROM locally,
and every pair says so.

Four guards, all in code rather than instructions to anyone:

1. ALLOWLIST. Only repositories listed in corpus/n64_sources.json AND given a recipe in
   corpus/pair_sources.json, hosted on github.com, are fetched.
2. EVALUATION BLOCK. The Snowboard Kids decompositions are the evaluation targets (SBK1 IDO,
   SBK2 GCC). Their repositories are refused by URL, and every fetched function is compared
   against their source: an exact normalized match or a near-duplicate is dropped and counted.
   If the evaluation source is not present the grabber refuses to run -- a guard that cannot see
   what it guards is not a guard.
3. PINNED AND UNEXECUTED. The checkout must be the pinned commit and the Makefile must hash to the
   value the flags were transcribed from. No repository code runs: no make, no scripts, only git
   and the compiler.
4. OUTSIDE THE REPO. Third-party source is written under the corpus directory, never into this
   project, and pairs are training data only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from tools import n64_corpus
from tools.synthetic_corpus import OBJDUMP, features, function_listing

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "corpus" / "n64_sources.json"
RECIPES = ROOT / "corpus" / "pair_sources.json"
CORPUS_DIR = Path.home() / "decomp" / "corpus"
PAIRS_DIR = Path.home() / "decomp" / "corpus-pairs"
GENERATED_ROOT = Path.home() / "decomp" / "corpus-generated"
EVALUATION_SOURCES = (Path.home() / "decomp" / "sbk1" / "src", Path.home() / "decomp" / "sbk2" / "src")
# Any repository naming the game: decomps, but also recomps and ports, which carry the decomp's symbols and types.
# The exact names `snowboardkids-decomp`/`snowboardkids2-decomp` admitted `cdlewis/snowboardkids2-recomp` (2026-10-03).
BLOCKED_REPOSITORY_NAMES = ("snowboardkids", "snowboard-kids", "snowboard_kids")
ALLOWED_HOSTS = {"github.com"}
TOOLCHAINS = {"ido-5.3": Path.home() / "decomp" / "sbk1" / "tools" / "ido-recomp" / "linux" / "cc"}

NEAR_DUPLICATE = 0.8          # sketch overlap at or above this is treated as the same function
MIN_TOKENS_FOR_SIMILARITY = 40  # below this the bottom-k proxy calls everything similar
GLOBAL_ASM = re.compile(r"\b(GLOBAL_ASM|INCLUDE_ASM|INCLUDE_RODATA)\s*\(")


class Refused(RuntimeError):
    """A guard declined. Never caught silently: it ends the run with its reason."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- guards ------------------------------------------------------------------

def admissible(url: str) -> None:
    """Refuse a repository URL unless it is on an allowed host and is not an evaluation target."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise Refused(f"host not allowlisted: {url}")
    lowered = url.lower()
    if any(name in lowered for name in BLOCKED_REPOSITORY_NAMES):
        raise Refused(f"evaluation repository is never a training source: {url}")


def load_recipes(allowlist: Path = ALLOWLIST, recipes: Path = RECIPES) -> dict[str, dict]:
    """Recipes joined to the allowlist; an id missing from either side is refused.

    One repository can pin several build variants: DKR ships five ROM revisions out of one source
    tree, and the flags differ per revision (`-DVERSION_$(REGION)_$(VERSION)`). Each variant is its
    own selector -- `dkr` is the default, `dkr@pal.v80` the other -- and they share a checkout,
    because the checkout is keyed by the allowlist id. A recipe written with a bare `flags` list,
    as the first one was, is a single unnamed variant, so nothing recorded earlier changes meaning.
    """
    allowed = {r["id"]: r for r in n64_corpus.load_manifest(allowlist)["repositories"]}
    joined = {}
    for recipe in json.loads(recipes.read_text(encoding="utf-8"))["repositories"]:
        if recipe["id"] not in allowed:
            raise Refused(f"recipe for {recipe['id']} has no allowlist entry")
        variants = recipe.get("variants") or {None: {"flags": recipe["flags"]}}
        default = recipe.get("default_variant")
        shared = {key: value for key, value in recipe.items() if key != "variants"}
        for name, variant in variants.items():
            selector = recipe["id"] if name in (None, default) else f"{recipe['id']}@{name}"
            if selector in joined:
                raise Refused(f"two recipes both answer to {selector}")
            entry = {**allowed[recipe["id"]], **shared, **variant, "key": selector, "variant": name}
            admissible(entry["url"])
            joined[selector] = entry
    return joined


def _fingerprint(body: str) -> tuple[str, tuple[int, ...], int]:
    tokens, _constants, _calls = n64_corpus.normalized_tokens(body)
    return _sha256("\x1f".join(tokens).encode()), n64_corpus.sketch(tokens), len(tokens)


@dataclass
class EvaluationIndex:
    """Normalized fingerprints of every evaluation-target function."""
    exact: set[str] = field(default_factory=set)
    sketches: list[tuple[int, ...]] = field(default_factory=list)
    postings: dict[int, list[int]] = field(default_factory=dict)
    functions: int = 0

    def add(self, body: str) -> None:
        digest, sketch, length = _fingerprint(body)
        self.functions += 1
        self.exact.add(digest)
        if length >= MIN_TOKENS_FOR_SIMILARITY:
            index = len(self.sketches)
            self.sketches.append(sketch)
            for value in sketch:
                self.postings.setdefault(value, []).append(index)

    @classmethod
    def build(cls, roots=EVALUATION_SOURCES) -> "EvaluationIndex":
        index = cls()
        for root in roots:
            if not Path(root).is_dir():
                raise Refused(f"evaluation source missing, cannot guard against it: {root}")
            for path in sorted(Path(root).rglob("*.c")):
                for fn in n64_corpus.extract_functions(path.read_text(encoding="utf-8", errors="replace")):
                    index.add(str(fn["body"]))
        if not index.functions:
            raise Refused("evaluation index is empty; refusing to run an unguarded grab")
        return index

    def verdict(self, body: str) -> str:
        """'clean', 'exact-duplicate' or 'near-duplicate'."""
        digest, sketch, length = _fingerprint(body)
        if digest in self.exact:
            return "exact-duplicate"
        if length < MIN_TOKENS_FOR_SIMILARITY or not sketch:
            return "clean"
        hits: dict[int, int] = {}
        for value in sketch:
            for candidate in self.postings.get(value, ()):
                hits[candidate] = hits.get(candidate, 0) + 1
        needed = NEAR_DUPLICATE * len(sketch)
        for candidate, shared in hits.items():
            if shared >= needed or n64_corpus.sketch_similarity(sketch, self.sketches[candidate]) >= NEAR_DUPLICATE:
                return "near-duplicate"
        return "clean"


# --- fetch -------------------------------------------------------------------

def _git(repo: Path, *args: str, timeout: int = 600) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, timeout=timeout).stdout.strip()


def fetch(entry: dict, corpus_dir: Path = CORPUS_DIR) -> Path:
    """Clone if absent, check out the pinned commit, verify the Makefile the flags came from."""
    admissible(entry["url"])
    repo = corpus_dir / entry["id"]
    if not (repo / ".git").is_dir():
        corpus_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", "--no-checkout", entry["url"], str(repo)],
                       check=True, timeout=900, capture_output=True, text=True)
    if _git(repo, "rev-parse", "HEAD") != entry["commit"]:
        _git(repo, "checkout", "--quiet", "--detach", entry["commit"])
    if _git(repo, "rev-parse", "HEAD") != entry["commit"]:
        raise Refused(f"{entry['id']}: checkout is not the pinned commit")
    # Line endings normalized: the first transcription hashed a Windows-side clone with CRLF and
    # refused the byte-identical upstream file. The check is about content, not checkout style.
    makefile = _sha256((repo / "Makefile").read_bytes().replace(b"\r\n", b"\n"))
    if makefile != entry["makefile_sha256"]:
        raise Refused(f"{entry['id']}: Makefile changed since its flags were transcribed ({makefile[:12]})")
    return repo


# --- pairs -------------------------------------------------------------------

def source_files(repo: Path, entry: dict) -> list[Path]:
    files = []
    for root in entry["source_roots"]:
        for path in sorted((repo / root).rglob("*.c")):
            rel = path.relative_to(repo).as_posix()
            if not any(pattern in f"/{rel}" for pattern in entry.get("exclude_path_patterns", [])):
                files.append(path)
    return files


def generated_include_dirs(entry: dict) -> list[Path]:
    """Directories holding headers a sandboxed extraction produced, for the `-I` path.

    `include/enums.h` does `#include "asset_enums.h"`, so the header has to be reachable as
    `asset_enums.h` from an include root: the collected output keeps its repo-relative path
    (`include/asset_enums.h`), and its PARENT becomes the root. Empty until `tools.sandbox_extract`
    has run, which `grab` treats as a refusal rather than as a game with nothing to do.
    """
    plan = entry.get("extraction")
    if not plan:
        return []
    base = GENERATED_ROOT / entry.get("key", entry["id"]).replace("@", ".")
    dirs = {base / Path(pattern).parent for pattern in plan.get("outputs", ())}
    return sorted(directory for directory in dirs if directory.is_dir())


def generated_headers(entry: dict) -> list[dict]:
    """The generated headers a grab will compile against, hashed for the receipt."""
    rows = []
    for directory in generated_include_dirs(entry):
        for path in sorted(directory.iterdir()):
            if path.is_file():
                rows.append({"path": str(path), "sha256": _sha256(path.read_bytes())})
    return rows


def per_file_flags(entry: dict, relative: str) -> list[str]:
    """Flags the repository's Makefile adds for this one source file.

    DKR's `src/get_stack_pointer.c` needs `-dollar`; without it cfe reports `Unknown character $
    ignored` and the file fails. A recipe that ignores per-file rules silently loses whatever files
    depend on them, which is how this one was missed for a whole session.
    """
    return [flag for rule in entry.get("per_file_flags", ()) if rule["path"] == relative
            for flag in rule["flags"]]


def compile_file(repo: Path, entry: dict, path: Path, workdir: Path) -> tuple[Path | None, str]:
    cc = TOOLCHAINS[entry["compiler"]]
    relative = path.relative_to(repo).as_posix()
    obj = workdir / (hashlib.sha1(str(path).encode()).hexdigest()[:16] + ".o")
    # Generated headers first: they are the repository's own generated include content, and the
    # tree does not carry a competing copy.
    includes = [flag for directory in generated_include_dirs(entry)
                for flag in ("-I", str(directory))]
    proc = subprocess.run([str(cc), "-c", *includes, *entry["flags"], *per_file_flags(entry, relative),
                           "-o", str(obj), relative],
                          cwd=repo, capture_output=True, text=True, timeout=300)
    return (obj if proc.returncode == 0 and obj.exists() else None), (proc.stderr or "")[-600:]


CONDITIONAL = re.compile(r"(?m)^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b")


def conditional_free(source: str, definition: str) -> bool:
    """True when no preprocessor conditional can change what this definition compiles to.

    The extractor does not preprocess, so a conditional that spans a function boundary makes the
    text and the object disagree. DKR's `dmacopy` at v77: `#if VERSION >= VERSION_79` removes its
    closing brace AND `dmacopy_internal`'s header, so the compiled `dmacopy` is dmacopy_internal's
    body -- a pair built from the text would teach C that does not produce the bytes beside it.
    A matched pair must be exact, so any conditional inside the definition, or an unclosed one
    before it, excludes the function.
    """
    if CONDITIONAL.search(definition):
        return False
    start = source.find(definition)
    if start < 0:
        return False
    depth = 0
    for match in CONDITIONAL.finditer(source[:start]):
        kind = match.group(1)
        depth += 1 if kind.startswith("if") else -1 if kind == "endif" else 0
    return depth == 0


def pairs_for_file(repo: Path, entry: dict, path: Path, index: EvaluationIndex,
                   workdir: Path, receipt: dict) -> list[dict]:
    source = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(repo).as_posix()
    if GLOBAL_ASM.search(source):
        receipt["files_skipped_rom_assembly"].append(rel)
        return []
    if per_file_flags(entry, rel):
        receipt["per_file_rules_fired"][rel] = receipt["per_file_rules_fired"].get(rel, 0) + 1
    functions = list(n64_corpus.extract_functions(source))
    if not functions:
        return []
    obj, stderr = compile_file(repo, entry, path, workdir)
    if obj is None:
        receipt["files_failed"].append({"file": rel, "stderr": stderr})
        return []
    receipt["files_compiled"] += 1
    dump = subprocess.run([OBJDUMP, "-dr", "--no-show-raw-insn", str(obj)],
                          capture_output=True, text=True, check=True).stdout
    rows = []
    for fn in functions:
        receipt["functions_seen"] += 1
        if not conditional_free(source, str(fn["definition"])):
            receipt["functions_conditional"] = receipt.get("functions_conditional", 0) + 1
            continue
        guard = index.verdict(str(fn["body"]))
        if guard != "clean":
            receipt["guard_rejected"][guard] = receipt["guard_rejected"].get(guard, 0) + 1
            continue
        listing = function_listing(dump, str(fn["name"]))
        if not listing:
            receipt["functions_without_code"] += 1        # declared but not emitted (e.g. unused static)
            continue
        asm = "\n".join(listing)
        rows.append({
            "schema_version": 1, "repository": entry["id"], "variant": entry.get("variant"),
            "commit": entry["commit"], "file": rel,
            "function": fn["name"], "line": fn["line"], "compiler": entry["compiler"],
            "source": fn["definition"], "source_sha256": _sha256(str(fn["definition"]).encode()),
            "asm": asm, "asm_sha256": _sha256(asm.encode()), "features": features(listing),
            "guard": guard, "verification": "upstream-matched; not verified against a ROM locally",
        })
    return rows


def grab(selector: str, out_dir: Path = PAIRS_DIR, corpus_dir: Path = CORPUS_DIR,
         index: EvaluationIndex | None = None) -> dict:
    """Grab one recipe selector: `dkr` for the default variant, `dkr@pal.v80` for another."""
    entry = load_recipes()[selector]
    if entry.get("extraction") and not generated_include_dirs(entry):
        # Declining here is the point. Without it the run "succeeds" with most files failed on a
        # missing include, which reads as a hard repository rather than as a step nobody ran.
        # Checked before the toolchain so the reason is the same on any host.
        raise Refused(
            f"{selector} declares an extraction step but its outputs are not present; "
            f"run: python3 -m tools.sandbox_extract {selector} --receipt <path>")
    cc = TOOLCHAINS.get(entry["compiler"])
    if cc is None or not cc.exists():
        raise Refused(f"toolchain unavailable for {entry['compiler']}")
    index = index or EvaluationIndex.build()
    repo = fetch(entry, corpus_dir)
    receipt = {"repository": entry["id"], "variant": entry.get("variant"), "selector": selector,
               "commit": entry["commit"], "compiler": entry["compiler"],
               "compiler_sha256": _sha256(cc.read_bytes()), "flags_sha256": _sha256(json.dumps(entry["flags"]).encode()),
               "generated_headers": generated_headers(entry),
               "evaluation_functions_indexed": index.functions, "started_at": int(time.time()),
               "files_considered": 0, "files_compiled": 0, "files_failed": [], "files_skipped_rom_assembly": [],
               "functions_seen": 0, "functions_without_code": 0, "guard_rejected": {}, "pairs": 0,
               "per_file_rules_fired": {}}
    work = Path(tempfile.mkdtemp(prefix=f"grab-{entry['id']}-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{selector.replace('@', '.')}.jsonl"
    try:
        with out.open("w", encoding="utf-8") as handle:
            for path in source_files(repo, entry):
                receipt["files_considered"] += 1
                for row in pairs_for_file(repo, entry, path, index, work, receipt):
                    handle.write(json.dumps(row) + "\n")
                    receipt["pairs"] += 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # A declared per-file rule that never fires is a finding, not a shrug: either the path is wrong
    # or the rule is being applied nowhere. Both silently lose the files that needed it.
    declared = {rule["path"] for rule in entry.get("per_file_flags", ())}
    silent = sorted(rel for rel in declared
                    if rel not in receipt["per_file_rules_fired"] and (repo / rel).exists())
    receipt["per_file_rules_silent"] = silent
    if silent:
        raise Refused(f"{selector}: per-file rule declared for {silent} but never applied")
    receipt["pairs_path"] = str(out)
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repositories", nargs="+",
                    help="selectors from corpus/pair_sources.json, e.g. dkr or dkr@pal.v80")
    ap.add_argument("--receipt", type=Path, required=True)
    args = ap.parse_args(argv)
    index = EvaluationIndex.build()
    receipts = [grab(repo_id, index=index) for repo_id in args.repositories]
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipts, indent=2) + "\n")
    for r in receipts:
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in r.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
