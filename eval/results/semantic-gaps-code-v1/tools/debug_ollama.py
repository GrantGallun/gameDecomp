"""Dump the raw shape of an ollama response, to see what a model actually returns."""

import json
import subprocess
import sys
import urllib.request

model = sys.argv[1] if len(sys.argv) > 1 else "gpt-oss:20b"
gw = subprocess.run(["bash", "-lc", "ip route show default | awk '{print $3}'"],
                    capture_output=True, text=True).stdout.strip()

payload = json.dumps({
    "model": model,
    "prompt": 'Write a C function `int addTwo(int a, int b)` that returns a+b. '
              'Output only one ```c code block.',
    "stream": False,
    "options": {"temperature": 0.2, "num_predict": 400},
}).encode()

req = urllib.request.Request(f"http://{gw}:11434/api/generate", data=payload,
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=300) as r:
    data = json.loads(r.read())

print("KEYS:", sorted(data.keys()))
for key in ("response", "thinking"):
    val = data.get(key)
    print(f"\n--- {key} ({len(val) if val else 0} chars) ---")
    if val:
        print(val[:900])
