"""Prefill, done properly this time.

Arm C of the previous test appended "```c" to the prompt and called
/api/generate -- which applies the chat template, so the text landed inside the
USER message. That is trailing text, not a prefill, and 18/18 still refused. It
tested nothing.

Real prefill puts a partial ASSISTANT message in the conversation, so the model
continues it rather than starting a fresh reply. /api/chat accepts that.
"""
import json
import sqlite3
import urllib.request
from pathlib import Path

from solver import llm, pipeline, workspace

repo = Path.home() / "decomp/sbk1"
conn = sqlite3.connect(str(Path.home()) + "/decomp/kb-sbk1.sqlite")
ep, MODEL, DRAWS = llm.host(), "gpt-oss:20b", 3
FUNCS = ["drawMenuAsciiFontTile", "gameThreadMain",
         "updateRaceSplitscreenSelectPlayerCountIcons"]


def chat(prompt: str, prefill: str | None) -> str:
    msgs = [{"role": "user", "content": prompt}]
    if prefill is not None:
        msgs.append({"role": "assistant", "content": prefill})
    body = {"model": MODEL, "messages": msgs, "stream": False,
            "think": "low",
            "options": {"temperature": 0.7, "num_predict": 6000,
                        "num_ctx": 32768, "num_thread": 12}}
    req = urllib.request.Request(f"{ep}/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=420) as r:
        d = json.loads(r.read())
    m = d.get("message", {})
    return (m.get("content") or m.get("thinking") or "")


tot = {"chat, no prefill": [0, 0], "chat, PREFILLED": [0, 0]}
for fn in FUNCS:
    ws = workspace.bootstrap(repo, fn)
    asm = workspace.target_asm(ws, fn)
    p = pipeline.build_prompt(repo, conn, fn, asm, workspace.m2c_draft(ws),
                              "reshape", use_siblings=False)
    line = f"  {fn[:40]:42}"
    for label, pre in (("chat, no prefill", None),
                       ("chat, PREFILLED", '```c\n#include "common.h"\n')):
        ref = 0
        for _ in range(DRAWS):
            try:
                out = chat(p, pre)
            except Exception as exc:
                print(f"    {label}: ERROR {type(exc).__name__}")
                continue
            full = (pre or "") + out
            tot[label][1] += 1
            if llm.is_refusal(out) or llm.is_refusal(llm.extract_c(full)):
                ref += 1
                tot[label][0] += 1
        line += f"  {label.split(',')[1].strip()}={ref}/{DRAWS}"
    print(line, flush=True)

print("\n===== REAL PREFILL =====")
for k, (r, n) in tot.items():
    print(f"  {k:20} {r:2}/{n:2} refused  ({100*r/max(1,n):3.0f}%)")
print("\nprevious fake prefill (text appended to the user turn): 18/18 refused")
