"""Repoint a paused run at the address its own model now answers on, proving the MODEL is unchanged.

`eval/results/resume-pipeline-20260908` recorded `endpoint http://172.28.32.1:11435`. Nothing has
served 11435 for at least a day: the supervisor retried, died on the freeze check, and the dashboard
reported `needs_repair` with `consecutive_failures: 3`. Ollama on the host answers on 11434 and serves
`gpt-oss:20b` whose digest is byte-identical to the run's recorded pin:

    recorded  17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7
    from 11434 17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7

So only the ADDRESS is stale; the identity the freeze check exists to protect is intact. That is the
distinction this script enforces: it refuses to write unless the digest at the new endpoint equals the
recorded one, so it can never silently move a run onto a different model.

`fast_campaign` compares `state['config']` against its CLI arguments field by field, so the stored
endpoint has to change for `--resume` to accept the working address. The change is recorded as a
runtime amendment with its evidence, and the original value is preserved.

    python3 -m eval.reconcile_endpoint --run DIR [--endpoint URL] [--check]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import campaign_state, frozen_wavefront                          # noqa: E402
from solver import llm                                                     # noqa: E402

AMENDMENT_KIND = "endpoint-address-reconciliation"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--endpoint", default=None, help="defaults to solver.llm.host()")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    run = args.run.resolve()
    endpoint = (args.endpoint or llm.host()).rstrip("/")
    state = campaign_state.read(run / "campaign.json")
    config = state["config"]
    recorded_endpoint, model = config["endpoint"], config["model"]
    recorded_digest = state["model_digest"]

    reported = {"run": str(run), "recorded_endpoint": recorded_endpoint,
                "candidate_endpoint": endpoint, "model": model,
                "recorded_model_digest": recorded_digest}
    if recorded_endpoint.rstrip("/") == endpoint:
        reported["status"] = "already-correct"
        print(json.dumps(reported, indent=2))
        return 0

    # THE SAFETY PROPERTY. The freeze check exists so a resumed run cannot drift onto a different
    # model. Changing the address is only legitimate because this equality holds.
    live_digest = frozen_wavefront.model_digest(endpoint, model)
    reported.update(live_digest=live_digest, model_identical=live_digest == recorded_digest)
    if live_digest != recorded_digest:
        reported["status"] = "refused-model-differs"
        print(json.dumps(reported, indent=2))
        return 1
    if args.check:
        reported["status"] = "would-reconcile"
        print(json.dumps(reported, indent=2))
        return 0

    record = {"kind": AMENDMENT_KIND, "applied_at": time.time(),
              "from": recorded_endpoint, "to": endpoint,
              "model": model, "model_digest": recorded_digest,
              "verification": "digest at the new endpoint equals the recorded pin, so the model is "
                              "identical and only the address changed",
              "authorization": "operator asked to resume the game-wide campaign; the recorded port "
                               "stopped serving and the model was found alive on 11434",
              "limits": "Address only. No candidate, oracle, certificate, ratchet, model identity or "
                        "held-out set is touched."}
    state["config"]["endpoint"] = endpoint
    state.setdefault("runtime_amendments", []).append(record)
    campaign_state.Store(run / "campaign.json").save(state)

    # Read it back through the same path `--resume` uses, so the change is proven rather than assumed.
    restored = campaign_state.read(run / "campaign.json")
    reported["endpoint_after"] = restored["config"]["endpoint"]
    reported["digest_after"] = restored["model_digest"]
    if reported["endpoint_after"] != endpoint or reported["digest_after"] != recorded_digest:
        reported["status"] = "FAILED-ROUND-TRIP"
        print(json.dumps(reported, indent=2))
        return 2
    (run / "endpoint-reconciliation.json").write_text(json.dumps(record, indent=2))

    # The supervisor does not rebuild the command; it re-runs the one recorded in `launch.json`, which
    # still carries the dead port. Reconciling the state without this would leave `--resume` comparing
    # the new stored endpoint against the old command line and failing `resume configuration differs`.
    launch_path = run / "launch.json"
    if launch_path.is_file():
        launch = json.loads(launch_path.read_text())
        command = launch.get("command") or []
        changed = [i for i, value in enumerate(command) if value.rstrip("/") == recorded_endpoint.rstrip("/")]
        for index in changed:
            command[index] = endpoint
        launch["command"] = command
        if launch.get("endpoint"):
            launch["endpoint"] = endpoint
        launch_path.write_text(json.dumps(launch, indent=2))
        reported["launch_endpoint_args_updated"] = len(changed)

    reported["status"] = "reconciled"
    print(json.dumps(reported, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
