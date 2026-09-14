"""Windows-side bridge from the WSL solver to the Codex CLI.

WSL interop is disabled on this machine and the CLI is a Windows npm install,
so the solver cannot exec it directly. Inference already crosses this boundary
over the default gateway for Ollama, so this serves the same shape on another
port and `solver/codexprovider.py` stays a plain HTTP client.

The isolation guards live HERE, in the argument list, rather than in the prompt:
the agent runs in a fresh empty directory with the user config and its hooks
ignored, and every command it executes is reported back so a trial that touched
anything can be rejected. A prompt cannot argue with argv.

That matters more than usual for this experiment. The reference decomp is the
answer key, and an agent that greps it has not solved anything. Be precise about
what each layer actually buys, because `-s read-only` is the one that invites
over-claiming:

* `read-only` blocks WRITES and network, not reads. The agent can still run
  commands and read files, so it is not by itself a contamination guard.
* What keeps the answer key away is location: the reference decomp lives in the
  WSL filesystem, and this process is on the Windows side, which reaches it only
  through the \\\\wsl.localhost share.
* The audit is therefore the real check, and it is the reason the tool-call
  parser is tested against a captured tool-using run rather than an assumed
  event shape.

Run (Windows, not WSL):

    python tools/codex_shim.py --port 11436
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_MODEL = "gpt-5.3-codex-spark"

# A spent quota must never look like a model with nothing to say. The CLI
# prints this on stdout and exits 1, so without an explicit check the kernel
# records `empty-response` -- the same thing it records for a genuine decline --
# and a cohort quietly fills up with fabricated nulls.
QUOTA_MARKERS = ("usage limit", "rate limit", "quota", "too many requests",
                 "try again at")

# Anything matching these is the agent acting on the world rather than
# answering. None should appear in a pure generation trial.
#
# Two shapes exist and BOTH are checked, because the one this CLI actually
# emits is the nested one: `{"type": "item.completed", "item": {"type":
# "command_execution", ...}}`. A marker test against the outer event type alone
# reports a clean run while the agent shells out -- verified against a real
# tool-using trial on codex-cli 0.144.3, not assumed from the flat shape.
TOOL_ITEM_TYPES = ("command_execution", "file_change", "patch_apply",
                   "mcp_tool_call", "web_search", "todo_list")
TOOL_EVENT_MARKERS = ("exec_command", "patch_apply", "mcp_tool_call",
                      "web_search", "apply_patch", "turn_diff")

_CALL_LOCK = threading.Lock()


class QuotaExhausted(RuntimeError):
    """The account's model quota is spent. Not a result; stop the run."""


def codex_executable() -> str:
    """The npm shim is a .cmd on Windows; PATHEXT resolution needs the name."""
    for candidate in ("codex.cmd", "codex"):
        found = shutil.which(candidate)
        if found:
            return found
    raise FileNotFoundError("codex CLI not found on PATH")


def build_command(*, executable: str, model: str, workdir: Path,
                  schema_path: Path | None, output_path: Path) -> list[str]:
    """Argument list for one isolated, non-interactive generation.

    Every flag here is a guard, so they are asserted in tests:
      -C empty dir + -s read-only   nothing of ours is visible or writable
      --ephemeral                   no session files accumulate between calls
      --ignore-user-config          user hooks/config cannot enter the trial
      --json                        the event stream we audit for tool use
    """
    command = [executable, "exec", "-",
               "-m", model,
               "-C", str(workdir),
               "--skip-git-repo-check",
               "--ephemeral",
               "-s", "read-only",
               "--ignore-user-config",
               "--color", "never",
               "-o", str(output_path),
               "--json"]
    if schema_path is not None:
        command += ["--output-schema", str(schema_path)]
    return command


def audit_events(stdout: str) -> dict:
    """Summarize the JSONL event stream: tokens spent and tools touched.

    Event shapes have moved between CLI versions (`{"msg": {...}}` and flat
    `{"type": ...}` both occur), so read defensively and count what is found
    rather than asserting a schema we do not control.
    """
    tool_calls: dict[str, dict] = {}
    types: dict[str, int] = {}
    tokens = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
    parsed = 0
    for index, line in enumerate(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        parsed += 1
        message = event.get("msg") if isinstance(event.get("msg"), dict) else event
        kind = str(message.get("type", ""))
        types[kind] = types.get(kind, 0) + 1

        item = message.get("item")
        if isinstance(item, dict):
            item_kind = str(item.get("type", ""))
            if item_kind in TOOL_ITEM_TYPES:
                # started and completed describe ONE call; key on the item id
                key = str(item.get("id") or f"{item_kind}-{index}")
                tool_calls[key] = {
                    "type": item_kind,
                    "command": item.get("command") or item.get("query") or "",
                    "cwd": item.get("cwd", ""),
                    "exit_code": item.get("exit_code"),
                }
        elif any(marker in kind for marker in TOOL_EVENT_MARKERS):
            tool_calls[f"{kind}-{index}"] = {
                "type": kind,
                "command": message.get("command") or message.get("call") or "",
                "cwd": message.get("cwd", ""),
            }

        usage = message.get("usage")
        if not isinstance(usage, dict) and "token" in kind:
            info = message.get("info") or {}
            usage = info.get("total_token_usage") or info
        if isinstance(usage, dict):
            for name, field in (("input", "input_tokens"),
                                ("output", "output_tokens"),
                                ("reasoning", "reasoning_output_tokens"),
                                ("total", "total_tokens")):
                value = int(usage.get(field) or 0)
                if value:
                    tokens[name] = value
    if not tokens["total"]:
        tokens["total"] = tokens["input"] + tokens["output"]
    calls = list(tool_calls.values())
    return {"tool_calls": calls, "tool_call_count": len(calls),
            "event_types": types, "events_parsed": parsed, "tokens": tokens}


def generate(payload: dict, *, executable: str | None = None,
             runner=subprocess.run) -> dict:
    """One isolated Codex generation. Returns text plus an audit receipt."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    model = str(payload.get("model") or DEFAULT_MODEL)
    timeout = int(payload.get("timeout") or 600)
    schema = payload.get("response_schema")

    executable = executable or codex_executable()
    with tempfile.TemporaryDirectory(prefix="codex-shim-") as temporary:
        root = Path(temporary)
        workdir = root / "workdir"        # deliberately empty: nothing to read
        workdir.mkdir()
        output_path = root / "last.txt"
        schema_path = None
        if isinstance(schema, dict) and schema:
            schema_path = root / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

        def invoke(schema_argument: Path | None):
            command = build_command(executable=executable, model=model,
                                    workdir=workdir, schema_path=schema_argument,
                                    output_path=output_path)
            with _CALL_LOCK:              # one account, one rate limit, one call
                # encoding is NOT optional: text mode defaults to the Windows
                # locale codepage (cp1252 here), so one character outside it --
                # a box-drawing rule in a diff, a non-breaking space -- made the
                # CLI reject the whole prompt as invalid UTF-8. That arrived at
                # the kernel as "empty-response", i.e. indistinguishable from a
                # model that had nothing to say about a hard function.
                return runner(command, input=prompt, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)

        def answer() -> str:
            if output_path.exists():
                return output_path.read_text(encoding="utf-8", errors="replace")
            return ""

        started = time.monotonic()
        completed = invoke(schema_path)
        text = answer()
        # The CLI takes only the strict structured-output subset, and the
        # kernel's proposal schema is not in it (oneOf, pattern, minItems), so
        # it exits before generating. The prompt already states the JSON shape
        # and the kernel validates every proposal, so retry unconstrained --
        # recorded, never silent, because "no schema" is a real protocol
        # difference between the arms.
        schema_fallback = False
        if schema_path is not None and (completed.returncode != 0
                                        or not text.strip()):
            schema_fallback = True
            first_stderr = completed.stderr or ""
            completed = invoke(None)
            text = answer()
            completed.stderr = (completed.stderr or "") + \
                "\n[schema rejected, retried without --output-schema] " + \
                first_stderr[-400:]
        elapsed = time.monotonic() - started

    audit = audit_events(completed.stdout or "")
    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    quota_line = next((line.strip() for line in combined.splitlines()
                       if any(marker in line.lower() for marker in QUOTA_MARKERS)), "")
    # One line per call, because the kernel reports an empty answer as
    # "empty-response" with no way to tell a refusal from a CLI error.
    print(f"[codex-shim] call model={model} prompt_chars={len(prompt)} "
          f"schema={'fallback' if schema_fallback else 'yes' if schema else 'no'} "
          f"rc={completed.returncode} "
          f"elapsed={elapsed:.1f}s text_chars={len(text)} "
          f"tools={audit['tool_call_count']} "
          f"tokens={audit['tokens']['total']}", flush=True)
    if quota_line:
        print(f"[codex-shim] QUOTA EXHAUSTED: {quota_line}", flush=True)
    elif not text.strip():
        # stderr alone was not enough: the CLI puts its real errors on stdout.
        print(f"[codex-shim] EMPTY ANSWER "
              f"stderr={(completed.stderr or '')[-400:]!r} "
              f"stdout={(completed.stdout or '')[-400:]!r}", flush=True)
    if quota_line:
        raise QuotaExhausted(quota_line)
    return {
        "schema_version": SCHEMA_VERSION,
        "text": text,
        "meta": {
            "model": model,
            "provider": "codex-cli",
            "returncode": completed.returncode,
            "elapsed_seconds": elapsed,
            "eval_count": audit["tokens"]["output"],
            "prompt_tokens": audit["tokens"]["input"],
            "total_tokens": audit["tokens"]["total"],
            "schema_requested": bool(schema),
            "schema_fallback": schema_fallback,
            "tool_call_count": audit["tool_call_count"],
            "tool_calls": audit["tool_calls"],
            "event_types": audit["event_types"],
            "events_parsed": audit["events_parsed"],
            "stderr_tail": (completed.stderr or "")[-2000:],
            "stdout_tail": (completed.stdout or "")[-2000:],
            "isolation": {"workdir": "empty temporary directory",
                          "sandbox": "read-only",
                          "user_config": "ignored"},
        },
    }


class Handler(BaseHTTPRequestHandler):
    model = DEFAULT_MODEL

    def _send(self, code: int, body: dict) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path != "/health":
            self._send(404, {"error": "not found"})
            return
        try:
            version = subprocess.run([codex_executable(), "--version"],
                                     capture_output=True, text=True,
                                     timeout=60).stdout.strip()
        except Exception as exc:                      # noqa: BLE001
            self._send(503, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._send(200, {"status": "ok", "codex_version": version,
                         "default_model": self.model,
                         "schema_version": SCHEMA_VERSION})

    def do_POST(self):  # noqa: N802 - stdlib naming
        if self.path != "/generate":
            self._send(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._send(400, {"error": f"invalid JSON body: {exc}"})
            return
        payload.setdefault("model", self.model)
        try:
            self._send(200, generate(payload))
        except QuotaExhausted as exc:
            # 429, so the caller can abort a cohort instead of recording nulls.
            self._send(429, {"error": str(exc), "quota_exhausted": True})
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "codex call timed out"})
        except Exception as exc:                      # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        print(f"[codex-shim] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="WSL reaches Windows on the default gateway, "
                             "not loopback")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    Handler.model = args.model
    print(f"[codex-shim] {codex_executable()} model={args.model} "
          f"listening on {args.bind}:{args.port}", flush=True)
    ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
