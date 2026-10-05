"""Reproduce the audit's finding 5 against the CURRENT code: can `verify()` verify the shapes `freeze()` emits?

THE CLAIM (`docs/deepseek-progress-audit-20260921.md` finding 5, recorded in
`eval/results/codex-audit-20260921/reproductions.json` under `manifest_verification`). Freezing two
generations through the real `eval.generation_manifest.freeze()` and then calling `verify()` on them
produced:

    directory-model : IsADirectoryError   "[Errno 21] Is a directory: .../model"
    inline-prompt   : KeyError            "'path'"

Both are shapes the freezer itself emits -- `base_model={"path": <a directory>, "files": []}` and
`prompts={"system": {"sha256": <hex>, "chars": N}}` -- and `compositions` was never looked at at all. A
caller that wrapped the call read the crash as "not verified"; a caller that did not crashed the stage.

WHAT THIS DOES. Drives the real `freeze()`/`verify()` pair on a scratch tree for those two shapes, plus the
coordinator's own component set from `eval/rsi_loop.stage_frozen`, and then makes two positive controls
FIRE: a changed file inside the directory tree, and a changed `compositions` entry. No model, no compiler,
no network, no clock.

It exits non-zero if `verify()` raises on any shape, if a component that cannot be recomputed by content
is reported as verified, if a recorded change is not caught, or if the changed components are not named.

HISTORY IS READ, NOT REWRITTEN. The pre-fix errors are quoted from the audit's checked-in
`reproductions.json`, which this script only reads.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from eval import generation_manifest as gm                                        # noqa: E402

AUDIT_RECEIPT = ROOT / "eval/results/codex-audit-20260921/reproductions.json"
RECEIPT = Path(__file__).with_name("reproduce_manifest.json")
failures: list[str] = []
receipt: dict = {}


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'ok ' if condition else 'FAIL'}] {name}{(' -- ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(f"{name}{(' -- ' + detail) if detail else ''}")


def verify_without_raising(out: Path, generation_id: str) -> dict | None:
    """The call under test. A raise here is the defect, so it is caught and reported as one."""
    try:
        return gm.verify(out, generation_id)
    except Exception as exc:                                    # noqa: BLE001 - that IS the finding
        check(f"verify({generation_id}) returns a report", False,
              f"raised {type(exc).__name__}: {exc}")
        receipt.setdefault("raised", {})[generation_id] = f"{type(exc).__name__}: {exc}"
        return None


def summarise(report: dict, base: Path) -> dict:
    """The part of a report a receipt should keep, with scratch paths made run-independent.

    Paths are kept RELATIVE to the scratch tree so two runs of this script produce the same bytes: the
    receipt is evidence to diff, and a random temporary directory would make every run look different.
    """
    def relative(value):
        try:
            return Path(value).relative_to(base).as_posix()
        except (TypeError, ValueError):
            return value

    def cleaned(item: dict) -> dict:
        return {**item, **({"path": relative(item["path"])} if item.get("path") else {})}

    return {"generation": report["generation"], "status": report["status"],
            "verified": report["verified"],
            # The violation strings carry paths too, and a scratch directory name would make the receipt
            # differ on every run; the tree root becomes `<scratch>` so the wording stays readable.
            "problems": [problem.replace(str(base), "<scratch>") for problem in report["problems"]],
            "unverifiable": [cleaned(entry) for entry in report["unverifiable"]],
            "checked": [cleaned(item) for item in report["checked"]],
            "hash_modes": report["hash_modes"], "components": report["components"],
            "manifest_mode": report["manifest"]["mode"], "fingerprint": report["fingerprint"]}


@contextmanager
def scratch_tree():
    """A FIXED scratch root rather than a random temporary directory.

    Artifact paths are part of every manifest, so a random directory name changes every fingerprint and
    makes two runs of this script write different receipts. The tree is removed before and after the run,
    so a previous interrupted run cannot leave behind a manifest that `freeze()` would refuse to overwrite
    under the same id.
    """
    root = Path(tempfile.gettempdir()) / "gameDecomp-reproduce-manifest"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    print("=" * 78)
    print("BEFORE THIS FIX (quoted from the audit's checked-in receipt, not rewritten here)")
    print("=" * 78)
    historical = {}
    if AUDIT_RECEIPT.exists():
        historical = (json.loads(AUDIT_RECEIPT.read_text("utf-8"))
                      .get("manifest_verification") or {})
    for name in ("directory-model", "inline-prompt"):
        recorded = historical.get(name) or {"note": "not present in the audit receipt"}
        print(f"  {name:16s} -> {json.dumps(recorded)}")
    receipt["audit_receipt"] = str(AUDIT_RECEIPT.relative_to(ROOT)) if AUDIT_RECEIPT.exists() else None
    receipt["historical_errors"] = historical
    receipt["scratch_root"] = "<temp>/gameDecomp-reproduce-manifest"
    receipt["generation_manifest_sha256"] = hashlib.sha256(
        Path(gm.__file__).read_bytes()).hexdigest()

    with scratch_tree() as root:
        out = root / "manifests"

        # --- shape 1: the base-model DIRECTORY, exactly as the audit built it ------------------
        print()
        print("=" * 78)
        print("SHAPE 1  base_model = {'path': <directory>, 'files': []}")
        print("=" * 78)
        model = root / "model"
        model.mkdir()
        gm.freeze(out, gm.Generation(id="directory-model", created_at="audit",
                                     base_model={"path": str(model), "files": []}))
        report = verify_without_raising(out, "directory-model")
        if report is not None:
            print(f"  status={report['status']} verified={report['verified']} "
                  f"problems={report['problems']}")
            check("the directory is not opened as a file", not report["problems"], str(report["problems"]))
            check("an empty frozen tree is verified by content", report["status"] == "verified",
                  f"status={report['status']} unverifiable={report['unverifiable']}")
            receipt["directory_model"] = summarise(report, root)
            # positive control: a changed file INSIDE the tree must fail, or the check does nothing
            (model / "config.json").write_text('{"model_type": "qwen2"}', encoding="utf-8")
            gm.freeze(out, gm.Generation(id="directory-model-files", created_at="audit",
                                         base_model={"path": str(model),
                                                     "files": [gm.hash_artifact(model / "config.json")]}))
            fire = verify_without_raising(out, "directory-model-files")
            check("a frozen tree with a listed file verifies", fire is not None
                  and fire["status"] == "verified", f"status={fire and fire['status']}")
            (model / "config.json").write_text('{"model_type": "llama"}', encoding="utf-8")
            fire = verify_without_raising(out, "directory-model-files")
            check("a changed file inside the tree is caught",
                  fire is not None and fire["status"] == "violated"
                  and f"content changed: {model / 'config.json'}" in fire["problems"],
                  f"problems={fire and fire['problems']}")
            receipt["directory_model_changed_file"] = summarise(fire, root) if fire else None

        # --- shape 2: the INLINE PROMPT digest, exactly as the audit built it ------------------
        print()
        print("=" * 78)
        print("SHAPE 2  prompts = {'system': {'sha256': <hex>, 'chars': 6}}")
        print("=" * 78)
        digest = gm.sha256_text("prompt")
        gm.freeze(out, gm.Generation(id="inline-prompt", created_at="audit",
                                     prompts={"system": {"sha256": digest, "chars": 6}}))
        report = verify_without_raising(out, "inline-prompt")
        if report is not None:
            print(f"  status={report['status']} verified={report['verified']} "
                  f"problems={report['problems']}")
            named = [entry for entry in report["unverifiable"]
                     if entry["component"] == "prompts.system"
                     and entry.get("recorded_sha256") == digest]
            check("the inline digest is reported, with the digest it holds", bool(named))
            check("an inline digest is NOT reported as verified", report["verified"] is False,
                  f"verified={report['verified']} status={report['status']}")
            check("nothing is claimed to have moved", report["problems"] == [], str(report["problems"]))
            receipt["inline_prompt"] = summarise(report, root)

        # --- shape 3: the DECLARED-ABSENT artifact, which `{"exists": False}` alone cannot express --
        print()
        print("=" * 78)
        print("SHAPE 3  memory = {'path': ..., 'exists': False, 'absent_because': <why>}")
        print("=" * 78)
        notes = root / "notes.jsonl"
        gm.freeze(out, gm.Generation(
            id="declared-absent", created_at="audit",
            memory={"path": str(notes), "exists": False,
                    "absent_because": "no note has been written yet; S0 starts with an empty memory"}))
        report = verify_without_raising(out, "declared-absent")
        check("a declared absence the record explains is CHECKED, not a violation",
              report is not None and report["status"] == "verified" and report["problems"] == [],
              f"status={report and report['status']} problems={report and report['problems']}")
        if report is not None:
            receipt["declared_absent"] = summarise(report, root)
        notes.write_text('{"id": "note-1"}\n', encoding="utf-8")
        report = verify_without_raising(out, "declared-absent")
        check("a declared-absent artifact that appears is a VIOLATION",
              report is not None and report["status"] == "violated"
              and any("appeared since freeze" in problem for problem in report["problems"]),
              f"problems={report and report['problems']}")
        gm.freeze(out, gm.Generation(id="undeclared-absent", created_at="audit",
                                     memory={"path": str(root / "never-created.jsonl"),
                                             "exists": False}))
        report = verify_without_raising(out, "undeclared-absent")
        check("an absence the record does NOT explain is a VIOLATION, not a silent pass",
              report is not None and report["status"] == "violated"
              and report["problems"] == [f"missing artifact: {root / 'never-created.jsonl'}"],
              f"status={report and report['status']} problems={report and report['problems']}")
        if report is not None:
            receipt["undeclared_absent"] = summarise(report, root)

        # --- two more consequences of the same defect -----------------------------------------
        print()
        print("=" * 78)
        print("EXECUTION-RELEVANT COMPONENTS THE OLD LOOP NEVER LOOKED AT")
        print("=" * 78)
        gm.freeze(out, gm.Generation(id="compositions", created_at="audit",
                                     compositions=[{"id": "comp-1", "when": {"residual_kind": "structural"},
                                                    "prefer": ["redraft", "permute"],
                                                    "reason": "measured", "status": "confirmed"}]))
        before = verify_without_raising(out, "compositions")
        check("an inline compositions list verifies while the manifest is the frozen one",
              before is not None and before["status"] == "verified",
              f"status={before and before['status']}")
        manifest = out / "compositions.json"
        payload = json.loads(manifest.read_text("utf-8"))
        payload["compositions"][0]["prefer"] = ["redraft", "stop", "permute"]
        manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        after = verify_without_raising(out, "compositions")
        check("a changed compositions entry FAILS verification",
              after is not None and after["status"] == "violated"
              and any("manifest content changed" in problem for problem in after["problems"]),
              f"problems={after and after['problems']}")
        receipt["compositions_changed"] = summarise(after, root) if after else None

        # --- the coordinator's own component set ----------------------------------------------
        print()
        print("=" * 78)
        print("THE COORDINATOR SHAPE  eval/rsi_loop.stage_frozen, on scratch paths")
        print("=" * 78)
        base = root / "coordinator-model"
        base.mkdir()
        (base / "config.json").write_text("{}", encoding="utf-8")
        (base / "model.safetensors").write_bytes(b"stand-in weights")
        evaluator = root / "rsi_transfer.py"
        evaluator.write_text("# evaluator\n", encoding="utf-8")
        splits = root / "splits.json"
        splits.write_text('{"dev": ["f1"], "test": ["f2"]}', encoding="utf-8")
        verifier = root / "workspace.py"
        verifier.write_text("# verifier\n", encoding="utf-8")
        coordinator = gm.Generation(
            id="S0", created_at="audit",
            base_model={"path": str(base),
                        "files": [gm.hash_artifact(path) for path in
                                  sorted([*base.glob("*.safetensors"), *base.glob("*.json")])]},
            adapters=[gm.hash_artifact(evaluator)],
            prompts={"system": {"sha256": gm.sha256_text("SYSTEM"), "chars": 6}},
            tool_schema={"action_space": {"sha256": gm.sha256_text('{"redraft": 1}')}},
            memory={"path": str(root / "coordinator-notes.jsonl"), "exists": False,
                    "absent_because": "no note has been written yet; S0 starts with an empty memory"},
            compositions=[],
            verifier={"path": str(verifier), **gm.hash_artifact(verifier)},
            evaluator={"path": str(evaluator), **gm.hash_artifact(evaluator)},
            datasets={"splits": gm.hash_artifact(splits)},
            budgets={"model_calls": 12, "compiles": 72},
            notes="agent_version=1")
        gm.freeze(out, coordinator)
        report = verify_without_raising(out, "S0")
        if report is not None:
            print(f"  status={report['status']} verified={report['verified']}")
            print(f"  problems   = {report['problems']}")
            print(f"  unverifiable (component: reason)")
            for entry in report["unverifiable"]:
                print(f"    - {entry['component']}: {entry['reason']}")
            print(f"  checked {len(report['checked'])} artifacts, hash modes {report['hash_modes']}")
            checked_names = {Path(item["path"]).name for item in report["checked"] if item.get("path")}
            check("every file-backed component is checked",
                  {"config.json", "model.safetensors", "rsi_transfer.py", "splits.json",
                   "workspace.py"} <= checked_names, str(sorted(checked_names)))
            check("the inline digests are named as unverifiable, not as verified",
                  {entry["component"] for entry in report["unverifiable"]}
                  >= {"prompts.system", "tool_schema.action_space"})
            check("the declared-absent notebook is checked, not a false \"missing artifact\"",
                  not any("missing artifact" in problem for problem in report["problems"])
                  and report["components"]["memory"] == "checked",
                  str(report["problems"]))
            check("no inline component is folded into `verified`", report["verified"] is False,
                  f"status={report['status']}")
            receipt["coordinator_shape"] = summarise(report, root)

        # --- the rule that matters for every caller -------------------------------------------
        print()
        print("=" * 78)
        print("THE RULE: a report with unverifiable content is never `verified`")
        print("=" * 78)
        for name in ("directory-model", "inline-prompt", "S0"):
            one = verify_without_raising(out, name)
            if one is None:
                continue
            check(f"{name}: verified is False whenever unverifiable is non-empty",
                  not (one["unverifiable"] and one["verified"]),
                  f"unverifiable={len(one['unverifiable'])} verified={one['verified']}")

    receipt["failures"] = failures
    receipt["result"] = "PASS" if not failures else "FAIL"
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print()
    print("=" * 78)
    if failures:
        print(f"DEFECT PRESENT: {len(failures)} check(s) failed")
        for item in failures:
            print(f"  - {item}")
        print(f"receipt: {RECEIPT}")
        return 1
    print("DEFECT NOT PRESENT: verify() returns a report for every shape the freezer emits, names the "
          "components it cannot recompute, and catches a recorded change.")
    print(f"receipt: {RECEIPT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
