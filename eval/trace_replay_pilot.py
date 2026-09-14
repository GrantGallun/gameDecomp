"""Replay the campaign's current C for one function against recorded game calls.

    /home/grant/decomp/sbk1/.venv/bin/python -m eval.trace_replay_pilot \\
        --function loadRaceMotionJointAnimationFrame \\
        --recordings eval/results/runtime-trace-20260913/record-loadRaceMotionJointAnimationFrame-1 \\
        --out eval/results/runtime-trace-20260913/replay-loadRaceMotionJointAnimationFrame-1

Compiles the selected source in an isolated native workspace (the campaign's own
`compile_candidate`), derives callee arities and return registers from project
headers the way the semantic lane does, then replays every recording. A control
replays the ORIGINAL assembly as the candidate: it must pass on every usable
recording, or the replay machinery itself is wrong.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


from eval import campaign_data, campaign_runtime, dag_pipeline_pilot as dag
from solver import project_headers, trace_replay

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "eval/results/resume-pipeline-20260908"
REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite"


def campaign_node(function: str, detail_path: Path) -> dict:
    # The dashboard listens on Windows loopback only, so the caller saves
    # /api/function?name=... to a file first; nothing here writes campaign state.
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    if detail.get("name") != function:
        raise ValueError("function detail belongs to another function")
    art = RUN / "campaign-artifacts"
    for path in sorted(art.glob(f"*-{function}.*c"), reverse=True):
        if hashlib.sha256(path.read_bytes()).hexdigest() == detail["source_sha256"]:
            return {"source": str(path), "source_sha256": detail["source_sha256"],
                    "attempt_id": detail["attempt_id"], "address": detail["address"], "size": detail["size"]}
    raise ValueError("campaign source for the current attempt not found")


def executed_store_mutation(target_asm: str, recordings: list[dict], entry: int, size: int):
    """Known-wrong control: shift ONE store the recordings actually executed.

    Mutating the first store in the text proved meaningless: on
    updateRacePlayerMode16AerialTrick it sat on a one-time init path absent from
    five of six recordings, so the mutant "passed" for want of execution, not
    because replay was blind. The ORIGINAL is mutated (not the candidate), so a
    mutant that still passes can only mean the replay cannot see the change.
    """
    import re
    from solver import cfg
    executed = {(e[1] - entry) // 4 for r in recordings for e in r["events"]
                if e[0] == "w" and entry <= e[1] < entry + size}
    lines, index = target_asm.splitlines(), -1
    for number, raw in enumerate(lines):
        line = cfg.clean_line(raw)
        if not line or line.startswith("glabel "):
            continue
        label = cfg.LABEL.match(line)
        if label:
            line = (label.group(2) or "").strip()
            if not line:
                continue
        if not cfg.INSTRUCTION.match(line):
            continue
        index += 1
        store = re.match(r"^(\s*)(s[bhw])(\s+\$?\w+\s*,\s*)(-?0x[0-9a-fA-F]+|-?\d+)(\(\$?(\w+)\).*)$", raw)
        if store and index in executed and store.group(6) != "sp":
            shift = 1 if store.group(2) == "sb" else 2
            lines[number] = f"{store.group(1)}{store.group(2)}{store.group(3)}{int(store.group(4), 0) + shift:#x}{store.group(5)}"
            return "\n".join(lines) + "\n", {"instruction": index, "address": f"{entry + 4 * index:#010x}",
                                             "before": raw.strip(), "after": lines[number].strip()}
    return target_asm, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function", required=True)
    parser.add_argument("--recordings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--detail", type=Path, required=True, help="saved /api/function JSON")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    node = campaign_node(args.function, args.detail)
    compiled = campaign_runtime.compile_candidate(repo=REPO, db=DB, function=args.function,
                                                  node=node, folder=args.out)
    isolated, ws, _state, _annotated_target, candidate_asm = compiled
    # Same target form the semantic lane uses: the object dump. The splat-annotated
    # target.s spells relocations `%lo(sym + 0x2C)`, which the interpreter rejects,
    # so a function with any addend relocation would never replay from it.
    from solver import workspace
    target_asm = workspace.semantic_assembly((ws / "target_object_dump_normalized.s").read_text(), ws / "target.o")
    abi = dag.prototype_info(isolated, args.function, target_asm)
    arities, _contracts = dag._call_contracts(isolated, project_headers.called_functions(target_asm))
    symbols = campaign_data.symbol_map((REPO / "symbol_addrs.txt").read_text())
    returns = tuple(abi["return_registers"])

    recordings = [json.loads(p.read_text()) for p in args.recordings.glob("call-*.json")]
    mutated_asm, mutated_note = executed_store_mutation(target_asm, recordings, node["address"], node["size"])
    print("known-wrong mutation:", mutated_note)

    reports, controls, wrong = [], [], []
    for path in sorted(args.recordings.glob("call-*.json"), key=lambda p: int(p.stem.split("-")[1])):
        record = json.loads(path.read_text())
        common = dict(entry=node["address"], size=node["size"], arities=dict(arities),
                      return_registers=returns, symbol_map=symbols)
        try:
            report = trace_replay.replay(record, target_asm, candidate_asm, **common)
            control = trace_replay.replay(record, target_asm, target_asm, **common)
            known_wrong = (trace_replay.replay(record, target_asm, mutated_asm, **common)
                           if mutated_note else {"status": "no_mutation"})
        except trace_replay.UnusableRecording as error:
            report = control = known_wrong = {"status": "unusable", "reasons": [str(error)],
                                              "entry_ordinal": record.get("entry_ordinal")}
        report["recording"], control["recording"], known_wrong["recording"] = path.name, path.name, path.name
        reports.append(report)
        controls.append(control)
        wrong.append(known_wrong)
        print(json.dumps({"recording": path.name, "candidate": report["status"], "control": control["status"],
                          "known_wrong": known_wrong["status"], "known_wrong_divergence": known_wrong.get("divergence"),
                          "reasons": report.get("reasons"), "divergence": report.get("divergence")}, default=str)[:1100])

    text = trace_replay.prompt(reports)
    summary = {"function": args.function, "source_sha256": node["source_sha256"], "attempt_id": node["attempt_id"],
               "return_registers": returns, "arities": arities, "reports": reports, "controls": controls,
               "known_wrong_mutation": mutated_note, "known_wrong": wrong,
               "known_wrong_prompt": trace_replay.prompt(wrong), "prompt": text}
    (args.out / "replay.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print("\nPROMPT TEXT:\n" + text)


if __name__ == "__main__":
    main()
