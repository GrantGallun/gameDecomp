"""Freeze a WHOLE generation, not a model filename.

WHY A MANIFEST AND NOT A NAME. A generation here is a bundle: weights, prompts, renderer, tool schema,
verified-memory snapshot, enabled compositions, verifier identity, training data and budgets. Two
generations that differ only in a memory note are NOT a weight update, and a report that calls them the
same thing cannot say what caused a change. The manifest is also the tamper check: every artifact it
names carries a content hash, `verify()` recomputes them, and a generation whose bytes have moved is
refused rather than silently reused.

WHAT IS HASHED FULLY AND WHAT IS SAMPLED, stated rather than glossed: small artifacts (adapters,
configs, notebooks, datasets, source files) are hashed in full. Multi-gigabyte base weights are hashed
from their first and last mebibyte plus their size, because a full digest of a 15 GiB checkpoint is
minutes of I/O per generation and the sampled digest plus size is what actually distinguishes the
checkpoints on this machine. `hash_mode` records which was used for every file, so nobody has to
assume the stronger claim.

IMMUTABILITY. A manifest for an existing generation is never rewritten. Re-freezing the same id with
different content raises, because that is exactly the silent-overwrite failure a generation pointer
must not be able to hide. Changing a fixed component (verifier, evaluator, budgets) starts a NEW
generation id.

THE SHAPES `verify()` HAS TO SURVIVE, taken from the callers and not from imagination. The coordinator's
`stage_frozen` emits exactly these, and two of them made the first `verify()` raise rather than report:

  base_model  {"path": <a DIRECTORY>, "files": [<hash_artifact>, ...]}   a tree plus a file list
  adapters    [<hash_artifact>, ...]                                     plain files, sampled when huge
  prompts     {"system": {"sha256": <hex>, "chars": N}}                  an INLINE DIGEST, no file
  tool_schema {"action_space": {"sha256": <hex>}}                         an inline digest, no file
  memory      {"path": ..., "exists": False, "absent_because": <why>}     an artifact DELIBERATELY absent
  compositions [<Composition.as_dict()>, ...]                             inline data, no digest at all
  verifier / evaluator / datasets / training  hash_artifact entries
  budgets     {"model_calls": 12, ...}                                    inline numbers

`absent_because` is the emitter stating INTENT, and it is the only thing that separates the two meanings
`{"exists": False}` used to carry: an artifact that is deliberately empty at freeze time (S0's notebook,
before any note exists) and an artifact that has gone missing. The freezer emits the absent shape for a
path it was asked to hash and could not read, so an absence with NO stated intent is a violation --
`missing artifact` -- and the one legitimate absence is the one the emitter declared in advance. With a
truthy `absent_because`, an absent path is consistent with the record and is checked as declared-absent;
if the path EXISTS anyway, that is a violation however the record was written ("artifact appeared since
freeze"), because the component is no longer the one that was frozen.

So `verify()` reports THREE states and never raises: `verified` (every component checked by content and
nothing moved), `violated` (something moved or is missing -- `problems` names it) and `unverified`
(nothing is known to have moved, but a recorded identity cannot be recomputed from what the manifest
holds). `verified` is True only for the first. A bare digest of a prompt nobody can rehash is not
evidence that the prompt is unchanged, and folding it into a pass is the defect this module exists
around; `unverifiable` says which components could not be recomputed and carries the recorded digest for
whoever holds the other end of it.

WHERE THE INLINE COMPONENTS GET A CONTENT CHECK. `budgets` and `compositions` name nothing outside the
manifest, so a verifier that only rehashes files could not tell whether they moved -- and reporting them
as fine because they have no path is the silent-decline shape this project keeps catching. The freezer
therefore also records the digest of the manifest bytes themselves (`<id>.json.sha256`, written once,
never rewritten); an edited manifest -- a changed budget, a reordered composition -- no longer matches
it. A manifest frozen before this existed reports those components as `unverified` rather than as a
pass, because there is then no digest to compare them against. This catches silent overwrite and
accidental edits; it is not a signature by a key the writer does not hold, so a caller who edits the
manifest AND rewrites its digest file can still pass.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
FULL_HASH_LIMIT = 64 * 1024 * 1024      # hash in full below this
SAMPLE_BYTES = 1024 * 1024              # else hash the first and last mebibyte
SAMPLED_HASH_MODE = "first+last-1MiB+size"   # the name recorded for a sampled digest
MANIFEST_DIGEST_SUFFIX = ".sha256"      # freeze-time digest of the manifest file itself
TREE_SKIP_SUFFIXES = (".safetensors", ".bin", ".gguf", ".pt")
MAX_WALK_DEPTH = 8                      # deeper nesting is reported, never silently skipped


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_artifact(path: Path) -> dict:
    """Hash one artifact, fully when it is small and by sample when it is not."""
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "exists": False}
    size = path.stat().st_size
    if size <= FULL_HASH_LIMIT:
        return {"path": str(path), "exists": True, "bytes": size,
                "sha256": sha256_file(path), "hash_mode": "full"}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(SAMPLE_BYTES))
        handle.seek(max(0, size - SAMPLE_BYTES))
        digest.update(handle.read(SAMPLE_BYTES))
    return {"path": str(path), "exists": True, "bytes": size,
            "sha256": digest.hexdigest(), "hash_mode": SAMPLED_HASH_MODE}


def digest_again(path: Path, mode: str) -> dict | None:
    """Recompute an artifact digest THE WAY THE FREEZER DID, or None when the rule is not one we know.

    THE RECORDED MODE DECIDES, not today's thresholds. A 5 GiB checkpoint that was sampled must be
    compared as sampled, and a config that was hashed in full must still be hashed in full even if it has
    since grown past `FULL_HASH_LIMIT`. Re-deciding the rule here would compare a different claim than
    the manifest records and would quietly upgrade or weaken the identity that was frozen.
    """
    path = Path(path)
    size = path.stat().st_size
    if mode == "full":
        return {"sha256": sha256_file(path), "bytes": size, "hash_mode": "full"}
    if mode == SAMPLED_HASH_MODE:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            digest.update(handle.read(SAMPLE_BYTES))
            handle.seek(max(0, size - SAMPLE_BYTES))
            digest.update(handle.read(SAMPLE_BYTES))
        return {"sha256": digest.hexdigest(), "bytes": size, "hash_mode": SAMPLED_HASH_MODE}
    return None


def hash_tree(directory: Path, *, skip_suffixes=TREE_SKIP_SUFFIXES) -> list[dict]:
    """Every small file under a directory, in a stable order, with its digest."""
    directory = Path(directory)
    if not directory.exists():
        return [{"path": str(directory), "exists": False}]
    rows = []
    for child in sorted(directory.rglob("*")):
        if child.is_dir() or child.suffix in skip_suffixes:
            continue
        rows.append(hash_artifact(child))
    return rows


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class Generation:
    """One frozen generation. `parent` is a generation id, never a path."""
    id: str
    created_at: str
    parent: str | None = None
    base_model: dict = field(default_factory=dict)
    adapters: list[dict] = field(default_factory=list)
    prompts: dict = field(default_factory=dict)
    tool_schema: dict = field(default_factory=dict)
    memory: dict = field(default_factory=dict)
    compositions: list[dict] = field(default_factory=list)
    verifier: dict = field(default_factory=dict)
    evaluator: dict = field(default_factory=dict)
    training: dict = field(default_factory=dict)
    datasets: dict = field(default_factory=dict)
    budgets: dict = field(default_factory=dict)
    proposals: list[str] = field(default_factory=list)
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)

    def content_fingerprint(self) -> str:
        """A digest over everything EXCEPT the id and timestamp, so the id can be derived from it."""
        payload = self.as_dict()
        for key in ("id", "created_at"):
            payload.pop(key, None)
        return sha256_text(canonical(payload))[:16]


def manifest_digest_path(manifest_path: Path) -> Path:
    """Where the freeze-time digest of a manifest file lives."""
    manifest_path = Path(manifest_path)
    return manifest_path.with_name(manifest_path.name + MANIFEST_DIGEST_SUFFIX)


def _record_manifest_digest(manifest_path: Path) -> Path:
    """Record the digest of the manifest BYTES as they were frozen; never rewrite a different one.

    The manifest itself stays immutable -- this is derived data about it, written once next to it, and
    the only thing `verify()` has to compare an inline component (`budgets`, `compositions`) against.
    """
    manifest_path = Path(manifest_path)
    digest_path = manifest_digest_path(manifest_path)
    digest = sha256_file(manifest_path)
    if digest_path.exists():
        recorded = digest_path.read_text("utf-8").strip()
        if recorded != digest:
            raise FileExistsError(
                f"{manifest_path} does not match the digest recorded when it was frozen "
                f"({recorded[:16]}... recorded, {digest[:16]}... found); refusing to overwrite the record")
        return digest_path
    digest_path.write_text(digest + "\n", encoding="utf-8")
    return digest_path


def freeze(out_dir: Path, generation: Generation) -> Path:
    """Write a generation manifest; refuse to overwrite a different one under the same id."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{generation.id}.json"
    payload = generation.as_dict()
    if path.exists():
        existing = json.loads(path.read_text("utf-8"))
        if canonical(existing) != canonical(payload):
            raise FileExistsError(
                f"generation {generation.id} already exists with different content; generations are "
                f"immutable, so a change to a fixed component starts a NEW generation id")
        # The bytes on disk are the payload, so the digest of those bytes belongs with them. A manifest
        # written before this existed gets its digest recorded here rather than staying unverifiable.
        _record_manifest_digest(path)
        return path
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _record_manifest_digest(path)
    return path


def load(out_dir: Path, generation_id: str) -> Generation:
    payload = json.loads((Path(out_dir) / f"{generation_id}.json").read_text("utf-8"))
    return Generation(**payload)


def verify(out_dir: Path, generation_id: str) -> dict:
    """Recompute every content identity the manifest records -- and say what could not be recomputed.

    NEVER RAISES ON A MANIFEST `freeze()` WROTE. The first version assumed every entry was one openable
    file with a `path` and a digest, so two shapes the coordinator emits crashed it: a base-model
    DIRECTORY with a file list (`IsADirectoryError` on POSIX, `PermissionError` on Windows) and an
    inline prompt digest with no file at all (`KeyError: 'path'`). A caller that wrapped the call read
    the crash as "not verified"; a caller that did not crashed the stage. It also never looked at
    `compositions`, so an execution-relevant component was silently unverified.

    THREE STATES, NOT TWO. `status` is `verified`, `violated` or `unverified`:
      * `verified`   every execution-relevant component was checked by content and nothing moved.
      * `violated`   something moved, appeared, is missing, or the manifest itself changed since freeze.
      * `unverified` nothing is known to have moved, but at least one recorded identity cannot be
                     recomputed from what the manifest holds (an inline digest with no content, a tree
                     file with no frozen digest, a manifest frozen before digests were recorded).
    `verified` is True only for the FIRST state; `problems` holds the violations and `unverifiable`
    holds the honest gaps, each with the digest that was recorded for it.
    """
    out_dir = Path(out_dir)
    manifest_path = out_dir / f"{generation_id}.json"
    problems: list[str] = []
    unverifiable: list[dict] = []
    checked: list[dict] = []
    components: dict[str, str] = {}
    hash_modes: dict[str, int] = {}
    manifest_state = {"path": str(manifest_path),
                      "digest_path": str(manifest_digest_path(manifest_path)),
                      "recorded": None, "actual": None, "mode": "not-recorded"}

    def report(status: str, fingerprint):
        return {"generation": generation_id, "verified": status == "verified", "status": status,
                "problems": problems, "unverifiable": unverifiable, "checked": checked,
                "components": components, "hash_modes": hash_modes, "manifest": manifest_state,
                "manifest_sha256": manifest_state.get("actual"), "fingerprint": fingerprint}

    if not manifest_path.exists():
        problems.append(f"missing manifest: {manifest_path}")
        return report("violated", None)

    try:
        manifest_state["actual"] = sha256_file(manifest_path)
    except OSError as exc:
        problems.append(f"unreadable manifest: {manifest_path} ({type(exc).__name__}: {exc})")
        return report("violated", None)

    digest_path = manifest_digest_path(manifest_path)
    recorded_digest = None
    if digest_path.exists():
        recorded_digest = digest_path.read_text("utf-8").strip()
        manifest_state["recorded"] = recorded_digest
        if recorded_digest != manifest_state["actual"]:
            problems.append(f"manifest content changed since it was frozen: {manifest_path}")
            manifest_state["mode"] = "mismatch"
        else:
            manifest_state["mode"] = "verified"
    # With a freeze-time digest, an inline component is covered by content and a change to `budgets` or
    # `compositions` is a violation; without one there is nothing to compare them against.
    record_intact = manifest_state["mode"] == "verified"

    try:
        generation = load(out_dir, generation_id)
    except Exception as exc:                                    # noqa: BLE001 - a report, never a crash
        problems.append(f"manifest unreadable: {manifest_path} ({type(exc).__name__}: {exc})")
        return report("violated", None)

    def component_state(name: str, state: str) -> None:
        rank = {"empty": 0, "checked": 1, "unverifiable": 2, "violated": 3}
        if rank[state] >= rank.get(components.get(name, "empty"), 0):
            components[name] = state

    def top(component: str) -> str:
        return component.split(".", 1)[0].split("[", 1)[0]

    def flag_violation(component: str, message: str) -> None:
        problems.append(message)
        component_state(top(component), "violated")

    def flag_unverifiable(component: str, kind: str, reason: str, **extra) -> None:
        unverifiable.append({"component": component, "kind": kind, "reason": reason, **extra})
        component_state(top(component), "unverifiable")

    def inline(component: str, value) -> None:
        """Content that lives in the manifest body: an inline digest names something else, data does not."""
        if isinstance(value, dict) and "sha256" in value:
            extras = {key: item for key, item in value.items() if key != "sha256"}
            flag_unverifiable(
                component, "inline-digest",
                "inline digest with no content to recompute it from; the digest names content that lives "
                "outside the manifest, so only a holder of that content can confirm it",
                recorded_sha256=value.get("sha256"), frozen_manifest_intact=record_intact, **extras)
            return
        payload_sha256 = sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                default=str))
        if record_intact:
            checked.append({"component": component, "kind": "manifest-inline",
                            "payload_sha256": payload_sha256})
            component_state(top(component), "checked")
        else:
            flag_unverifiable(
                component, "manifest-inline",
                "inline content with no freeze-time manifest digest, so a change to it cannot be detected",
                payload_sha256=payload_sha256)

    def check_tree(component: str, entry: dict, directory: Path) -> None:
        """The directory shape: verify each listed file, then walk the tree `hash_tree` was written for."""
        listed = [child for child in (entry.get("files") or []) if isinstance(child, dict)]
        listed_paths = {str(Path(str(child["path"]))) for child in listed if child.get("path")}
        for index, child in enumerate(listed):
            walk(f"{component}.files[{index}]", child, 2)
        try:
            rows = hash_tree(directory)
        except OSError as exc:
            flag_unverifiable(component, "directory", path=str(directory),
                              reason=f"the tree could not be walked ({type(exc).__name__}: {exc})")
            return
        for row in rows:
            if row.get("exists") is False:
                flag_violation(component, f"missing artifact: {row['path']}")
                continue
            if str(Path(str(row.get("path")))) in listed_paths:
                continue                    # already compared against the digest frozen for it
            flag_unverifiable(
                f"{component}.tree", "unrecorded-tree-file", path=row.get("path"),
                hash_mode=row.get("hash_mode"),
                reason="present in the frozen tree but covered by no frozen digest, so a change to it "
                       "cannot be detected")
        checked.append({"component": component, "kind": "directory", "path": str(directory),
                        "listed_files": len(listed), "tree_files": len(rows),
                        "tree_skips": list(TREE_SKIP_SUFFIXES)})
        component_state(top(component), "checked")

    def check_artifact(component: str, entry: dict) -> None:
        path = Path(str(entry.get("path")))
        if entry.get("exists") is False:
            if path.exists():
                # The record says this path was absent when the manifest was frozen and it is not absent
                # any more. For S0 that is the empty notebook having been written afterwards, and the
                # memory component is no longer the one that was frozen -- whatever the record's intent.
                flag_violation(component, f"artifact appeared since freeze: {path}")
            elif entry.get("absent_because"):
                # DECLARED ABSENT, and the record says why: consistent with what was frozen, and there is
                # no content to hash because there was none. Checking that the absence still holds is the
                # whole of what can be established, and the reason is carried so a reader can weigh it.
                checked.append({"component": component, "kind": "declared-absent", "path": str(path),
                                "absent_because": entry["absent_because"]})
                component_state(top(component), "checked")
            else:
                # ABSENT WITH NO STATED INTENT IS A PROBLEM, not an ambiguity to shrug at.
                # `hash_artifact()` returns this shape for a path the freezer was ASKED to hash and could
                # not read, and for an adapter or a verifier file that absence means the generation is not
                # what the record says it is. The one legitimate absence -- S0's empty notebook -- is
                # exactly the one the emitter can declare in advance with `absent_because`, so the record
                # decides this rather than any component name.
                flag_violation(component, f"missing artifact: {path}")
            return
        if not path.exists():
            flag_violation(component, f"missing artifact: {path}")
            return
        if path.is_dir():
            check_tree(component, entry, path)
            return
        digest = entry.get("sha256")
        if not digest:
            flag_unverifiable(component, "unhashed-file", path=str(path),
                              reason="file named with no recorded digest, so a change cannot be detected")
            return
        mode = entry.get("hash_mode")
        if not mode:
            flag_unverifiable(component, "file", path=str(path), recorded_sha256=digest,
                              reason="digest recorded without a hash_mode, so the rule the freezer used "
                                     "is unknown and cannot be reproduced")
            return
        try:
            again = digest_again(path, mode)
        except OSError as exc:
            flag_unverifiable(component, "file", path=str(path), recorded_sha256=digest,
                              reason=f"file could not be read ({type(exc).__name__}: {exc})")
            return
        if again is None:
            flag_unverifiable(component, "file", path=str(path), recorded_sha256=digest,
                              reason=f"unknown hash_mode {mode!r}, so there is no rule to recompute with")
            return
        if again["sha256"] != digest:
            flag_violation(component, f"content changed: {path}")
            return
        if entry.get("bytes") is not None and entry["bytes"] != again["bytes"]:
            # The sampled rule hashes the first and last mebibyte; the recorded size is the part of a
            # sampled identity that a change outside the sample cannot hide.
            flag_violation(component, f"content changed: {path} "
                                      f"(size {entry['bytes']} -> {again['bytes']})")
            return
        checked.append({"component": component, "kind": "file", "path": str(path),
                        "hash_mode": mode, "bytes": again["bytes"]})
        hash_modes[mode] = hash_modes.get(mode, 0) + 1
        component_state(top(component), "checked")

    def walk(component: str, value, depth: int = 0) -> None:
        """Classify every leaf of a component, so a shape nobody anticipated is reported, not skipped."""
        if depth > MAX_WALK_DEPTH:
            flag_unverifiable(component, "nested",
                              reason=f"nested deeper than {MAX_WALK_DEPTH} levels; nothing to compare")
            return
        if isinstance(value, dict):
            if isinstance(value.get("files"), list):
                if "path" in value:
                    check_artifact(component, value)
                else:
                    flag_unverifiable(component, "directory",
                                      reason="a file list with no directory path: the entries are checked, "
                                             "the tree they belong to cannot be")
                    for index, child in enumerate(value["files"]):
                        walk(f"{component}.files[{index}]", child, depth + 1)
                return
            if "path" in value:
                check_artifact(component, value)
                for key, child in value.items():
                    if key not in ("path", "exists", "bytes", "sha256", "hash_mode", "absent_because"):
                        walk(f"{component}.{key}", child, depth + 1)
                return
            if "sha256" in value:
                inline(component, value)
                return
            if not value:
                component_state(top(component), "empty")
                return
            for key, child in value.items():
                walk(f"{component}.{key}", child, depth + 1)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(f"{component}[{index}]", child, depth + 1)
            return
        if value is None:
            return
        inline(component, value)

    for field_name, field_value in generation.as_dict().items():
        # id and created_at are identity, not content: the fingerprint is defined over everything else.
        if field_name in ("id", "created_at"):
            continue
        components.setdefault(field_name, "empty")
        walk(field_name, field_value)

    status = "violated" if problems else ("unverified" if unverifiable else "verified")
    return report(status, generation.content_fingerprint())
