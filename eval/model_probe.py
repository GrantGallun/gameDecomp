"""Print the model digest an endpoint serves, so a pin can be checked before a run is launched.

`frozen_wavefront.model_digest` is the freeze check every campaign run performs against its endpoint.
It is also what tells you whether a run pinned to `gpt-oss:20b` at some address can be resumed today,
or whether a new run should be bootstrapped instead. The live run recorded
`17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7` against
`http://172.28.32.1:11435`; `ollama list` on the host shows `gpt-oss:20b` with ID `17052f91a42e`.

    python3 -m eval.model_probe [--endpoint URL] [--model NAME] [--expect DIGEST]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval import frozen_wavefront                                          # noqa: E402
from solver import llm                                                     # noqa: E402

# The digest the live run pinned, from eval/results/resume-pipeline-20260908/launch.json.
RECORDED = "17052f91a42e97930aa6e28a6c6c06a983e6a58dbb00434885a0cf5313e376f7"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", default=None,
                    help="defaults to solver.llm.host(), the WSL gateway on ollama's port")
    ap.add_argument("--model", default="gpt-oss:20b")
    ap.add_argument("--expect", default=RECORDED)
    args = ap.parse_args(argv)

    endpoint = args.endpoint or llm.host()
    print(f"endpoint : {endpoint}")
    print(f"model    : {args.model}")
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/api/tags", timeout=10) as response:
            tags = json.loads(response.read())
        names = [m.get("name") for m in tags.get("models", [])]
        print(f"reachable: yes, {len(names)} models")
        print(f"has model: {args.model in names}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"reachable: NO ({type(exc).__name__}: {exc})")
        return 2
    digest = frozen_wavefront.model_digest(endpoint, args.model)
    print(f"digest   : {digest}")
    print(f"expected : {args.expect}")
    print(f"match    : {digest == args.expect}")
    return 0 if digest == args.expect else 1


if __name__ == "__main__":
    raise SystemExit(main())
