"""Would CURRENT main-tree code unpark the pipeline-parked functions?

Parking is terminal in the campaign (`next_profile` returns None), so a function
parked by a pipeline gap stays parked even after the gap is fixed in the main
tree. The live campaign runs frozen code and is not touched here: this replays
the campaign's own `_intake` with main-tree code, a copied database and a fresh
output directory, and records what each function does now.

The 27 functions parked as genuinely assembly-level are excluded on purpose.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/parked-probe-20260913/probe.py
"""
import json
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import completion_campaign                                   # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = Path.home() / "decomp/sbk1"
DB = Path.home() / "decomp/kb-sbk1-parkedprobe-20260913.sqlite"

PIPELINE_PARKED = {
    "recipe -mips3 (fixed in main 09-08, never deployed)": [
        "__ll_div", "__ll_lshift", "__ll_mod", "__ll_mul", "__ll_rem", "__ll_rshift",
        "__ull_div", "__ull_divremi", "__ull_rem", "__ull_rshift"],
    "object postprocessing backend": [
        "initRaceSetupMenu", "initRaceSetupSaveMenu", "raceSetupMenuNoop",
        "updateRaceSetupPlayerCountMenu", "updateRaceSetupRumblePrompt", "updateRaceSetupSaveMenu"],
    "intake: unbalanced function body": [
        "__osContGetInitData", "checkMainMenuSecretCode", "enqueueRacePlayerVoiceSound",
        "func_80063E70", "ldiv", "updateCharacterSelectCourseRecordsFrame",
        "updateShopMenuCourseListPanel"],
    "intake: requires one ordinary function definition": [
        "__osDevMgrMain", "allocateGameTask", "fixedCosine", "initAudioDmaCallback",
        "sprintf", "updateControllerPakFileDeleteMainOptions"],
    "target resolution": [
        "corrupted", "corrupted_init", "drawMenuAsciiCharImpl",
        "initMainMenuSceneModelRenderer_pad", "initRaceUiFadingImpact"],
    "out of memory 2026-09-09 (WSL then capped at 2 GB)": ["updateThrownTrailImpactProjectile"],
}


def probe(item):
    group, function = item
    out = HERE / "receipts" / f"{function}.json"
    out.parent.mkdir(exist_ok=True)
    started = time.monotonic()
    try:
        result = completion_campaign._intake(repo=REPO, db=DB, function=function, node={}, out=out)
        record = {"group": group, "function": function, "outcome": result.get("status", "evaluated"),
                  "score": result.get("score"), "exact": bool((result.get("residual") or {}).get("exact")),
                  "compiled": (result.get("residual") or {}).get("compiled"),
                  "blocker": result.get("blocker")}
    except Exception as exc:                                    # the campaign parks on these
        record = {"group": group, "function": function, "outcome": "raised",
                  "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                  "trace_tail": traceback.format_exc()[-600:]}
    record["seconds"] = round(time.monotonic() - started, 1)
    out.write_text(json.dumps(record, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({k: record.get(k) for k in ("function", "outcome", "score", "exact", "compiled", "error")},
                     default=str), flush=True)
    return record


def main():
    items = [(g, f) for g, fs in PIPELINE_PARKED.items() for f in fs]
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(probe, items))
    (HERE / "results.json").write_text(json.dumps(records, indent=2, default=str) + "\n", encoding="utf-8")
    print("done:", len(records))


if __name__ == "__main__":
    main()
