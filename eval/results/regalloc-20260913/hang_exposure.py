"""How many register-dominant campaign nodes would spin in the LIVE load_modify_stores regex.

    /home/grant/decomp/sbk1/.venv/bin/python eval/results/regalloc-20260913/hang_exposure.py

Read-only: hydrates the checkpoint, runs the frozen generator (and the main-tree
fix) on each queued regalloc_search node's current source with a 5 s alarm.
Writes hang-exposure.json.
"""
import importlib.util
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "eval/results/resume-pipeline-20260908"
sys.path.insert(0, str(ROOT))
from eval import campaign_state  # noqa: E402
from solver import regalloc_mutations as fixed  # noqa: E402

spec = importlib.util.spec_from_file_location("live_mutations", RUN / "code/solver/regalloc_mutations.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class Spin(Exception):
    pass


def alarm(_signum, _frame):
    raise Spin()


signal.signal(signal.SIGALRM, alarm)
state = campaign_state.read(RUN / "campaign.json")
queued = [name for name, item in state["repair_queue"]["work_items"].items() if item["profile"] == "regalloc_search"]
rows = []
for name in sorted(queued):
    source = Path(state["nodes"][name]["source"]).read_text()
    row = {"function": name}
    for label, module in (("live", live), ("fixed", fixed)):
        started = time.monotonic()
        signal.alarm(5)
        try:
            row[label] = len(list(module.load_modify_stores(source, name)))
        except Spin:
            row[label] = "spin"
        except (module.Decline, ValueError):
            row[label] = "declined"
        finally:
            signal.alarm(0)
        row[label + "_seconds"] = round(time.monotonic() - started, 2)
    rows.append(row)
spins = [r["function"] for r in rows if r["live"] == "spin"]
agree = all(r["live"] == r["fixed"] for r in rows if r["live"] != "spin")
(Path(__file__).with_name("hang-exposure.json")).write_text(json.dumps({"queued": len(rows), "live_spins": spins,
                                                                        "outputs_agree_elsewhere": agree, "rows": rows}, indent=1))
print(json.dumps({"queued": len(rows), "live_spins": spins, "fixed_spins": [r["function"] for r in rows if r["fixed"] == "spin"],
                  "outputs_agree_elsewhere": agree}))
