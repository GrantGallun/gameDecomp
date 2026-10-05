"""Print the probes and rule of induction runs (topic [seed])."""
import json
import sys

import run

topic = sys.argv[1]
seed = int(sys.argv[2]) if len(sys.argv) > 2 else None
for r in map(json.loads, open(run.E / "induction.jsonl")):
    if r["topic"] == topic and (seed is None or r["seed"] == seed):
        print(f"== {r['topic']} seed {r['seed']} (validation pairs it probed verbatim: {r['overlap']})")
        for i, h in enumerate(r["probes"], 1):
            print(f" {i}. [{h['opt']} -> {h['verdict']}] {h['hypothesis'][:110]}")
            print(f"      a: {h['a'][:110]}\n      b: {h['b'][:110]}")
        print(" RULE:", r["rule"])
        print(" prior:", r["prior"])
        print(" pred :", r["predictions"])
        print(" truth:", r["truth"])
