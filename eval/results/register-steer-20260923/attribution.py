"""Which steps were necessary? Index form alone, and with each earlier step, on the pre-steering best source."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "waitCourseSelectRecordsClose"
census = next(c for c in json.loads((HERE / "census.json").read_text()) if c["function"] == NAME)
best = census["best_source"]
begin, end = best.index("    var_s0 = 0;\n"), best.index("    updateCallbackTasks();")


def index_loop(call, reads):
    return ("    var_s0 = 0;\n    if ((s32) gPlayerCount > 0) {\n        do {\n"
            "            gCurrentMenuCameraObject = &D_801121E0[var_s0];\n" f"            {call}\n"
            "            var_s0 += 1;\n" + "            if (!var_s0);\n" * reads +
            "        } while (var_s0 < (s32) gPlayerCount);\n    }\n")


variants = {
    "index_only": index_loop("gCurrentMenuCameraObject->update();", 0),
    "index_member_call": index_loop("D_801121E0[var_s0].update();", 0),
    "index_plus_reads": index_loop("gCurrentMenuCameraObject->update();", 3),
    "index_reads_member_call": index_loop("D_801121E0[var_s0].update();", 3),
}
out = [{"function": NAME, "label": f"attr:{k}", "source": best[:begin] + v + best[end:]} for k, v in variants.items()]
(HERE / "probes-attr.json").write_text(json.dumps(out, indent=1))
print(len(out))
