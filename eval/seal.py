"""Sealed near-miss split for judging repair generators, with a checked look ledger.

``eval.clean_set`` freezes never-touched functions, which have no candidate to repair, so
it cannot test a generator. This freezes the other population: functions with a compiled,
never-exact candidate. The split is translation-unit-disjoint by a salted hash, and the
salt is the digest of a pre-registration file, so it cannot be shopped.

What makes it a measuring instrument rather than a convention:

* **Enforcement is by name.** The manifest is written as ``eval/sets/sbk1_v*.json`` with a
  ``heldout`` list, the shape ``zero_token_harvest.heldout_names`` and
  ``clean_set._set_names`` already read, so existing tools exclude the sealed side without
  being modified. ``assert_dev_only`` is for new tools; it is not the only gate.
* **The pool is built from every ledger.** Exact in the KB or the campaign store, in
  ``already_matched``, in any earlier frame or set: excluded. Each row pins the starting
  ``(ledger, attempt_id, source_sha256)`` so no tool picks its own start.
* **``look()`` checks, it does not accept.** It takes attempt ids for a control arm and a
  treatment arm, re-reads exact/score/source hash from the ledgers, hashes the tool's files
  itself, and requires the whole sealed set. The chain starts at the manifest digest, entry
  numbers must be contiguous, and the manifest carries a cap on looks.
* **Primary metric is paired best score, clustered by TU** (exact counts are secondary:
  with ~0 exacts at baseline they cannot move). ``power`` states the smallest detectable
  effect for the realised n before the first look.

Honest limits: sealed from the freeze date forward. Earlier tools saw these functions;
``prior_attempts`` is a covariate and results must be stratified by it. The ledger file can
still be deleted wholesale: commit each look's ``entry`` hash to git when it is written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

TIERS = (("tiny", 8), ("small", 20), ("medium", 50), ("large", 120), ("huge", 300))
EXCLUDE_TU = ("%ultra%", "%libmus%", "%libc%", "%audio%")
MAX_LOOKS = 3


def _digest(value: dict, drop: str = "manifest_digest") -> str:
    material = {k: v for k, v in value.items() if k != drop}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tier_of(insns) -> str:
    if insns is None:
        return "unknown"           # never silently "tiny": NULL is a data defect to report
    name = "tiny"
    for label, low in TIERS:
        if insns >= low:
            name = label
    return name


def is_sealed(tu: str, salt: str, fraction: float) -> bool:
    """TU-level: siblings share structs and idioms, so a TU is never split."""
    return hashlib.sha256(f"{salt}:{tu}".encode()).digest()[0] < int(fraction * 256)


def pool(ledgers: dict[str, sqlite3.Connection], min_score: float, excluded: set[str]) -> list[dict]:
    """Best compiled attempt per function across ledgers; never exact in ANY ledger.

    ``exact IS NULL`` is historical-unknown, not exact; those rows are counted per function
    so a reader can see how much of a function's history is unverified.
    """
    skip = " ".join(f"AND t.name NOT LIKE '{p}'" for p in EXCLUDE_TU)
    rows: dict[str, dict] = {}
    ever_exact: set[str] = set()
    for label, conn in ledgers.items():
        for (name,) in conn.execute(
                "SELECT DISTINCT f.name FROM functions f JOIN attempts a ON a.func_addr=f.addr WHERE a.exact=1"):
            ever_exact.add(name)
        for name, tu, insns, aid, score, sha, src in conn.execute(
                "SELECT f.name, t.name, f.insn_count, a.id, a.score, a.source_sha256, a.source_code "
                "FROM attempts a JOIN functions f ON f.addr=a.func_addr JOIN tus t ON t.id=f.tu_id "
                f"WHERE t.name LIKE '%src/%' {skip} AND a.compiled=1 AND a.score>=? ORDER BY f.name, a.score DESC, a.id",
                (min_score,)):
            if name in rows and rows[name]["best_score"] >= score:
                continue
            rows[name] = {"function": name, "tu": tu, "tier": tier_of(insns), "best_score": score,
                          "start": {"ledger": label, "attempt_id": aid,
                                    "source_sha256": sha or hashlib.sha256((src or "").encode()).hexdigest()}}
    out = []
    for name in sorted(rows):
        if name in ever_exact or name in excluded:
            continue
        r = rows[name]
        stats = [conn.execute("SELECT COUNT(*), COUNT(DISTINCT a.strategy), SUM(a.exact IS NULL) FROM attempts a "
                              "JOIN functions f ON f.addr=a.func_addr WHERE f.name=?", (name,)).fetchone()
                 for conn in ledgers.values()]
        r["prior_attempts"] = sum(s[0] for s in stats)
        r["prior_strategy_count"] = sum(s[1] for s in stats)
        r["unknown_exact_rows"] = sum(s[2] or 0 for s in stats)
        out.append(r)
    return out


def power(n_tus: int) -> dict:
    """Smallest detectable result for a TU-clustered exact sign test at alpha=.05 (two-sided)."""
    for k in range(1, n_tus + 1):
        if 2 * sum(math.comb(k, i) for i in range(k, k + 1)) / 2 ** k < 0.05:
            return {"sealed_tus": n_tus, "min_same_direction_tus": k,
                    "note": f"need {k} TUs all improving with 0 worsening to reach p<.05"}
    return {"sealed_tus": n_tus, "min_same_direction_tus": None,
            "note": "too few sealed TUs for any sign-test result; do not look"}


def freeze(ledgers: dict[str, sqlite3.Connection], *, prereg: Path, excluded: dict[str, set[str]],
           fraction: float = 0.3, min_score: float = 90.0, max_looks: int = MAX_LOOKS) -> dict:
    if not 0 < fraction < 1:
        raise ValueError("fraction must be in (0, 1)")
    salt = file_sha256(prereg)
    union = set().union(*excluded.values()) if excluded else set()
    dev, sealed = [], []
    for row in pool(ledgers, min_score, union):
        (sealed if is_sealed(row["tu"], salt, fraction) else dev).append(row)
    manifest = {
        "schema_version": 2, "kind": "sealed-near-miss-split", "created_at": int(time.time()),
        "salt": salt, "prereg_file": str(prereg), "fraction": fraction, "min_score": min_score,
        "max_looks": max_looks,
        "policy": {
            "selection_inputs": "ledger metadata and best score only; no source or residual read",
            "split": "tu-disjoint, salt = sha256(pre-registration file)",
            "enforcement": "saved under eval/sets/sbk1_v*.json with a heldout list, read by existing exclusion code",
            "primary_metric": "paired best-score sign test, clustered by TU; exact counts secondary",
            "exposure": "sealed from freeze date forward; prior_attempts is a covariate",
        },
        "exclusion_counts": {label: len(names) for label, names in excluded.items()},
        "power": power(len({r["tu"] for r in sealed})),
        "dev": dev, "sealed": sealed,
        # eval/sets contract: tools that exclude held-out names read this key.
        "heldout": [{"function": r["function"], "tier": r["tier"]} for r in sealed],
    }
    manifest["manifest_digest"] = _digest(manifest)
    return manifest


def sealed_names(manifest: dict) -> set[str]:
    return {r["function"] for r in manifest["sealed"]}


def assert_dev_only(names, manifest: dict) -> None:
    leaked = sorted(set(names) & sealed_names(manifest))
    if leaked:
        raise PermissionError(f"{len(leaked)} sealed functions in development input: {leaked[:5]}")


# Digests of every manifest that must be present. A copied or stale ``eval/`` tree has no
# manifest, and "no manifest" must never read as "nothing is sealed".
EXPECTED_SEALS = {"540ca87622b7fd8eddf26931d07f405b5f1cf124699445181d6a42ffb4ba7048"}


def _sealed_manifests(sets_dir: Path | None, expected: set[str] | None) -> list[dict]:
    import os
    directory = Path(sets_dir or os.environ.get("GAMEDECOMP_SETS_DIR") or Path(__file__).resolve().parent / "sets")
    required = EXPECTED_SEALS if expected is None else expected
    found: list[dict] = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            if "sealed" in path.name:
                raise RuntimeError(f"{path.name}: sealed manifest unreadable ({exc})") from exc
            continue
        if isinstance(blob, dict) and blob.get("kind") == "sealed-near-miss-split":
            if _digest(blob) != blob.get("manifest_digest"):
                raise RuntimeError(f"{path.name}: sealed manifest digest does not verify")
            found.append(blob)
    missing = required - {m["manifest_digest"] for m in found}
    if missing:
        raise RuntimeError(f"sealed manifest(s) {sorted(d[:12] for d in missing)} not found in {directory}; "
                           "a tree without the seal must not be used to select training or mining data")
    return found


def sealed_in_sets(sets_dir: Path | None = None, expected: set[str] | None = None) -> set[str]:
    """Sealed function names from every frozen near-miss manifest. Raises rather than returning less.

    The one call corpus-wide builders use. Empty, missing, stale or tampered manifests raise:
    a copied ``eval/`` without ``sets/`` otherwise yields an empty exclusion set silently.
    ``GAMEDECOMP_SETS_DIR`` overrides the directory. ``expected`` replaces the pinned digests (tests).
    """
    names: set[str] = set()
    for blob in _sealed_manifests(sets_dir, expected):
        names |= sealed_names(blob)
    if not names:
        raise RuntimeError("sealed exclusion set is empty")
    return names


def sealed_tus_in_sets(sets_dir: Path | None = None, expected: set[str] | None = None) -> set[str]:
    """Translation units that contain a sealed function; siblings share structs and idioms."""
    sealed_in_sets(sets_dir, expected)
    return {r["tu"] for blob in _sealed_manifests(sets_dir, expected) for r in blob["sealed"]}


def audit(manifest: dict) -> dict:
    dev = {r["function"] for r in manifest["dev"]}
    sealed = sealed_names(manifest)
    dev_tus = {r["tu"] for r in manifest["dev"]}
    sealed_tus = {r["tu"] for r in manifest["sealed"]}
    ok = _digest(manifest) == manifest.get("manifest_digest")
    heldout_ok = {h["function"] for h in manifest.get("heldout", [])} == sealed
    return {"clean": ok and heldout_ok and not dev & sealed and not dev_tus & sealed_tus,
            "manifest_digest_valid": ok, "heldout_list_matches_sealed": heldout_ok,
            "overlap_functions": sorted(dev & sealed), "overlap_tus": sorted(dev_tus & sealed_tus),
            "dev": len(dev), "sealed": len(sealed),
            "unknown_tier": sum(r["tier"] == "unknown" for r in manifest["sealed"])}


def _read(ledger: Path) -> list[dict]:
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]


def verify_ledger(ledger: Path, manifest: dict) -> dict:
    prev, bad = manifest["manifest_digest"], []      # chain is seeded by the manifest
    for i, e in enumerate(_read(ledger)):
        if (e.get("prev") != prev or _digest(e, "entry") != e.get("entry") or e.get("look_number") != i + 1
                or e.get("manifest") != manifest["manifest_digest"]):
            bad.append(i)
        prev = e.get("entry", "")
    return {"looks": len(_read(ledger)), "valid": not bad, "bad_entries": bad}


def _arm(ledgers: dict[str, sqlite3.Connection], ref: dict, name: str, start: dict | None = None) -> dict:
    conn = ledgers[ref["ledger"]]
    row = conn.execute("SELECT a.exact, a.score, a.compiled, a.source_sha256, a.source_code, f.name "
                       "FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE a.id=?",
                       (ref["attempt_id"],)).fetchone()
    if row is None or row[5] != name:
        raise ValueError(f"attempt {ref} is not an attempt of {name}")
    exact, score, compiled, sha, src, _ = row
    sha = sha or hashlib.sha256((src or "").encode()).hexdigest()
    return {"exact": bool(exact), "score": float(score or 0.0) if compiled else 0.0, "source_sha256": sha}


def look(manifest: dict, ledger: Path, ledgers: dict[str, sqlite3.Connection], *, tool: str,
         tool_files: list[Path], outcomes: dict[str, dict], budget: dict, spec: dict | None = None,
         spend: dict | None = None, extra: dict | None = None, exclude: set[str] | None = None,
         note: str = "") -> dict:
    """One evaluation of a tool on the sealed side.

    ``outcomes[name] = {"control": {ledger, attempt_id}, "treatment": {ledger, attempt_id}}``
    and ``budget = {"control_compiles": n, "treatment_compiles": m}`` (must be equal: the claim
    is "same cap per function, per arm; actual spend reported"). ``spend[name] = {"control": c,
    "treatment": t}`` is checked against the cap per function and stored. ``spec`` (treatment
    kwargs, budget, depth, beam...) is hashed with the tool files so two treatments on one tree
    are different tools. ``extra`` (errors, ties, divergence) is written INSIDE the entry before
    it is hashed. ``exclude`` names are dropped for a second, pre-declared sign test.
    """
    names = sealed_names(manifest)
    if set(outcomes) != names:
        raise ValueError(f"look must cover exactly the sealed set: missing {len(names - set(outcomes))}, "
                         f"extra {len(set(outcomes) - names)}")
    if budget.get("control_compiles") != budget.get("treatment_compiles"):
        raise ValueError("arms must have equal compiler-call budgets")
    cap = budget["control_compiles"]
    over = sorted(n for n, sp in (spend or {}).items() if max(sp.get("control", 0), sp.get("treatment", 0)) > cap)
    if over:
        raise ValueError(f"per-function spend exceeds the cap {cap}: {over[:5]}")
    check = verify_ledger(ledger, manifest)
    if not check["valid"]:
        raise RuntimeError(f"ledger invalid at {check['bad_entries']}")
    entries = _read(ledger)
    if len(entries) >= manifest["max_looks"]:
        raise RuntimeError(f"seal spent: {len(entries)} of {manifest['max_looks']} looks used")
    rows, by_name = [], {r["function"]: r for r in manifest["sealed"]}
    for name in sorted(names):
        control = _arm(ledgers, outcomes[name]["control"], name)
        treat = _arm(ledgers, outcomes[name]["treatment"], name)
        for label, arm in (("control", control), ("treatment", treat)):
            if arm["exact"] and arm["score"] != 100.0:
                raise ValueError(f"{name}: {label} exact attempt with score {arm['score']}")
        rows.append({"function": name, "tu": by_name[name]["tu"], "tier": by_name[name]["tier"],
                     "start": by_name[name]["start"]["source_sha256"], "control": control, "treatment": treat,
                     "refs": outcomes[name]})
    tool_hash = hashlib.sha256(("".join(file_sha256(p) for p in sorted(tool_files))
                                + json.dumps(spec or {}, sort_keys=True)).encode()).hexdigest()
    entry = {"manifest": manifest["manifest_digest"], "tool": tool, "tool_sha256": tool_hash, "spec": spec or {},
             "at": int(time.time()), "look_number": len(entries) + 1,
             "repeat_of_same_tool": any(e["tool_sha256"] == tool_hash for e in entries),
             "budget": budget, "spend": spend or {}, "extra": extra or {}, "rows": rows,
             "report": paired_report(rows), "note": note,
             "prev": entries[-1]["entry"] if entries else manifest["manifest_digest"]}
    if exclude:
        entry["report_excluding"] = {"excluded": sorted(exclude & names),
                                     **paired_report([r for r in rows if r["function"] not in exclude])}
    entry["entry"] = _digest(entry, "entry")
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    return entry


def paired_report(rows: list[dict]) -> dict:
    """Primary: TU-clustered sign test on (treatment - control) best score. Secondary: exact."""
    by_tu: dict[str, float] = {}
    for r in rows:
        by_tu[r["tu"]] = by_tu.get(r["tu"], 0.0) + r["treatment"]["score"] - r["control"]["score"]
    up = sum(v > 0 for v in by_tu.values())
    down = sum(v < 0 for v in by_tu.values())
    k, n = max(up, down), up + down
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n) if n else 1.0
    exact_t = {r["function"] for r in rows if r["treatment"]["exact"]}
    exact_c = {r["function"] for r in rows if r["control"]["exact"]}
    return {"tus": len(by_tu), "tus_up": up, "tus_down": down, "sign_test_p": p,
            "exact_new": sorted(exact_t - exact_c), "exact_lost": sorted(exact_c - exact_t),
            "by_stratum": stratified(rows)}


def stratified(rows: list[dict]) -> dict:
    """Exact new counts by tier x prior-attempt exposure is in the manifest; here by tier."""
    out: dict = {}
    for r in rows:
        cell = out.setdefault(r["tier"], {"n": 0, "exact_new": 0})
        cell["n"] += 1
        cell["exact_new"] += r["treatment"]["exact"] and not r["control"]["exact"]
    return out


def _excluded_inputs(args, ledgers) -> dict[str, set[str]]:
    from eval import clean_set, matched, zero_token_harvest
    root = Path(args.project_root)
    out = {"already_matched": matched.already_matched(next(iter(ledgers.values()))),
           "eval_sets": zero_token_harvest.heldout_names(root / "eval/sets") | clean_set._set_names(root / "eval/sets")}
    for path in args.exclude_frame or []:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
        out[Path(path).name] = {r["name"] if isinstance(r, dict) and "name" in r else
                                r.get("function") if isinstance(r, dict) else r for r in rows}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--ledger-db", action="append", required=True, help="label=path, e.g. kb=/x.sqlite")
    f.add_argument("--out", required=True)
    f.add_argument("--prereg", required=True)
    f.add_argument("--project-root", default=".")
    f.add_argument("--exclude-frame", action="append", help="JSON list of names/rows from an earlier frame")
    f.add_argument("--fraction", type=float, default=0.3)
    f.add_argument("--min-score", type=float, default=90.0)
    f.add_argument("--dry-run", action="store_true")
    a = sub.add_parser("audit")
    a.add_argument("manifest")
    a.add_argument("--ledger")
    args = ap.parse_args()
    if args.cmd == "freeze":
        ledgers = {}
        for spec in args.ledger_db:
            label, _, path = spec.partition("=")
            ledgers[label] = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        m = freeze(ledgers, prereg=Path(args.prereg), excluded=_excluded_inputs(args, ledgers),
                   fraction=args.fraction, min_score=args.min_score)
        summary = {"dev": len(m["dev"]), "sealed": len(m["sealed"]), "power": m["power"],
                   "exclusions": m["exclusion_counts"], "digest": m["manifest_digest"]}
        if not args.dry_run:
            out = Path(args.out)
            if out.exists():
                raise SystemExit(f"{out} exists; a sealed manifest is never overwritten")
            out.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
    else:
        m = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        report = audit(m)
        if args.ledger:
            report["ledger"] = verify_ledger(Path(args.ledger), m)
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["clean"] and report.get("ledger", {"valid": True})["valid"] else 1)


if __name__ == "__main__":
    main()
