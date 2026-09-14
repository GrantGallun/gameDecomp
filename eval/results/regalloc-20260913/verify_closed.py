"""Re-verify every closed register-allocation function from its saved source, in a fresh bench.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/regalloc-20260913/verify_closed.py

Writes closed/<function>.c and closed/index.json. A function counts as closed only
if this fresh compile is object-exact (the workspace.score byte certificate).
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eval import regalloc_probe  # noqa: E402

HAND = {  # found with `regalloc_probe probe`; each generator was then given a fire test on the same shape
    "randomNextObject": "manual/rno-scalar-postinc-reload.c",
    "updateEndingLindaHopRightToPose": "manual/g-updateEndingLindaHopRightToPose-postfix.c",
    "updateRaceUiCourseRecordRevealFinalMoney": "manual/g-updateRaceUiCourseRecordRevealFinalMoney-minus.c",
    "updateEndingSlashStartFinalPose": "manual/g3-updateEndingSlashStartFinalPose-unused-first.c",
    "_collectPVoices": "manual/m-_collectPVoices-while-body-load.c",
    "Fdrums": "manual/gen-Fdrums-0.c",
    "releaseRelocatableHeapBlockMetadata": "manual/q-releaseRelocatableHeapBlockMetadata-postfix.c",
    "updateRacePlayerShockEffect": "manual/gen-updateRacePlayerShockEffect.c",
    "updateRaceCameraFixedPositionFollow": "manual/gen-updateRaceCameraFixedPositionFollow.c",
    "updateEndingLindaBlinkThenSlideLeft": "manual/s-updateEndingLindaBlinkThenSlideLeft-03.c",
    "updateEndingSlashAfterVanishWait": "manual/s-updateEndingSlashAfterVanishWait-03.c",
    "updateRaceUiTrickPrizePayoutRevealMakeBonus": "manual/s-updateRaceUiTrickPrizePayoutRevealMakeBonus-03.c",
    "func_800628DC": "manual/t-func_800628DC-0.c",
    "initTitleMenuSparkle": "manual/gen-initTitleMenuSparkle.c",
    "initRaceUiPrizePayout": "manual/v-initRaceUiPrizePayout-typed-sum.c",
    "initRaceCourseScrollingTexture": "manual/gen-initRaceCourseScrollingTexture-bytes.c",
    "initCourseBillboardMarker": "manual/gen-initCourseBillboardMarker-bytes.c",
    "updateRaceCameraIntroPan": "manual/z-updateRaceCameraIntroPan-neg-first.c",
    "_doModFunc": "manual/aa-_doModFunc-reuse.c",
}
BYTE_LEVEL = {
    "__osSpGetStatus": ("manual/i-__osSpGetStatus-const.c",
                        "Constant-address IO read is instruction-identical (lui t6,0xa404 / lw v0,0x10(t6)); the target "
                        "object names the address through a relocation to SP_STATUS_REG, which no C spelling tried "
                        "reproduces. Close through whole-ROM verification."),
    "osAiGetLength": ("manual/i-osAiGetLength-const.c",
                      "Same as __osSpGetStatus for AI_LEN_REG (0xA4500004)."),
    "finishMainMenuDemoRaceIntro": ("manual/q-finishMainMenuDemoRaceIntro-postfix.c",
                                    "Every instruction matches (gradient 0,0,0); the target object carries 4 trailing "
                                    "alignment nops from its translation-unit layout (.text 160 vs 144 bytes). Close "
                                    "through isolated integration / whole-ROM verification."),
}
PARKED = {
    "drawCourseSelectExtraCourseBadge": "Registers exact; one u16 stack slot sits at 0x36 instead of 0x34. Declaration "
                                        "order, unused locals of several widths, array/struct wrappers and removing the "
                                        "m2c spill local did not place it. A stack-layout, not allocation, residual.",
}


def main():
    cohort = {r["name"]: r for r in json.loads((HERE / "cohort.json").read_text())["functions"]
              if r["cohort"] == "regalloc_only"}
    search4 = {json.loads(line)["function"]: json.loads(line) for line in (HERE / "search-4/summary.jsonl").open()}
    sources = {name: (f"search-4/{name}.exact.c", "search-4", row.get("family"))
               for name, row in search4.items() if row["outcome"] == "exact"}
    sources.update({name: (path, "hand", None) for name, path in HAND.items()})
    out = HERE / "closed"
    out.mkdir(exist_ok=True)
    index = {"checkpoint": json.loads((HERE / "cohort.json").read_text())["checkpoint"], "functions": {}}
    for name in sorted(cohort):
        if name in sources:
            path, how, family = sources[name]
        elif name in BYTE_LEVEL:
            path, how, family = BYTE_LEVEL[name][0], "byte_level", None
        else:
            index["functions"][name] = {"status": "parked", "reason": PARKED.get(name, "not closed")}
            continue
        text = (HERE / path).read_text()
        bench = regalloc_probe.Bench(cohort[name])
        try:
            result = bench.run(text, path)
        finally:
            bench.close()
        exact = bool(result["exact"])
        status = ("object_exact" if exact else
                  "byte_level_pending_rom_verification" if name in BYTE_LEVEL and result.get("gradient") in ([0, 0, 0], [2, 0, 0]) else
                  "FAILED_REVERIFICATION")
        entry = {"status": status, "found_by": how, "family": family, "source": f"closed/{name}.c",
                 "source_sha256": hashlib.sha256(text.encode()).hexdigest(), "gradient": result.get("gradient"),
                 "campaign_score": cohort[name]["score"], "campaign_attempt_id": cohort[name]["attempt_id"]}
        if name in BYTE_LEVEL:
            entry["reason"] = BYTE_LEVEL[name][1]
        shutil.copyfile(HERE / path, out / f"{name}.c")
        index["functions"][name] = entry
        print(json.dumps({"function": name, "status": status, "found_by": how}), flush=True)
    counts = {}
    for entry in index["functions"].values():
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    index["counts"] = counts
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
