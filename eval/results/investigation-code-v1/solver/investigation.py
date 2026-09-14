"""Campaign investigation tools. Observations and explanations stay separate.

Only header metadata, binary evidence and generated compiler experiments enter
these tools. No reference C bodies or model-authored KB evidence are imported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from solver import compiler_recipe, evidence_schedule, workspace


def question(node):
    from solver.repair_queue import lane, Lane
    phase = lane(node)
    alternatives = {
        Lane.FRONTEND: ["missing build/interface context", "incorrect source representation"],
        Lane.SEMANTIC: ["incorrect source behavior", "incomplete ABI or execution environment"],
        Lane.ENVIRONMENT: ["missing executable callee", "missing valid memory or device state"],
        Lane.BYTE: ["incorrect source types or expression structure", "compiler scheduling or lifetimes"],
        Lane.BLOCKED: ["unsupported tool capability", "incorrect target/build identity"],
    }.get(phase, ["insufficient target evidence", "candidate needs revalidation"])
    return {"question": "Which observation distinguishes the remaining causes?",
            "alternatives": alternatives, "lane": phase.value,
            "policy": "Predict an observation before editing. Treat supplied headers as assistance. "
                      "A compiler probe measures code generation, not target semantics."}


class Tools:
    def __init__(self, repo, ws, conn, function, output):
        self.repo, self.ws, self.conn = Path(repo), Path(ws), conn
        self.function, self.output = function, Path(output)
        self.receipts = []
        self.hypotheses = []

    def _record(self, kind, payload):
        record = {"kind": kind, "function": self.function, **payload}
        record["id"] = evidence_schedule.fingerprint(record)
        self.receipts.append(record)
        return json.dumps(record, sort_keys=True)

    def evidence(self, query):
        """Address-keyed observations plus direct caller/callee context."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", query):
            raise ValueError("evidence query must be a function symbol")
        row = self.conn.execute("SELECT addr FROM functions WHERE name=?", (query,)).fetchone()
        if row is None:
            return self._record("binary-observations", {"query": query, "status": "unknown-symbol"})
        columns = [r[1] for r in self.conn.execute("PRAGMA table_info(evidence)")]
        if not {"id", "func_addr"} <= set(columns):
            return self._record("binary-observations", {"query": query, "status": "schema-unavailable"})
        rows = self.conn.execute("SELECT * FROM evidence WHERE func_addr=? ORDER BY id LIMIT 65", (row[0],)).fetchall()
        observations = [dict(zip(columns, r)) for r in rows[:64]]
        return self._record("binary-observations", {"query": query, "address": row[0],
            "observations": observations, "truncated": len(rows) > 64,
            "authority": "imported binary observations; base identity/type interpretations remain hypotheses"})

    def probe(self, source, hypothesis):
        """Compile novel self-contained C with the target recipe; never run it."""
        if not hypothesis.strip() or not source.strip() or len(source) > 8000:
            raise ValueError("probe requires a prediction and at most 8000 source characters")
        # No preprocessor/file access, assembly or linkage to reference bodies.
        if re.search(r"#|\b(?:asm|__asm__|__asm|include|incbin)\b", source):
            raise ValueError("probe must be self-contained C without preprocessing or assembly")
        identity = self.ws / ".compiler-target.json"
        if not identity.exists():
            raise ValueError("compiler probe requires independently resolved TU identity")
        recipe = compiler_recipe.resolve(self.repo, json.loads(identity.read_text())["target"])
        key = evidence_schedule.fingerprint({"source": source, "recipe": recipe})
        directory = self.output / "probes" / key
        directory.mkdir(parents=True, exist_ok=True)
        c, obj = directory / "probe.c", directory / "probe.o"
        c.write_text(source, encoding="utf-8")
        command = [*recipe["command"], "-o", str(obj.resolve()), str(c.resolve())]
        process = subprocess.run(command, cwd=self.repo, capture_output=True, text=True, timeout=45)
        payload = {"prediction": hypothesis, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                   "recipe": recipe, "command": command, "returncode": process.returncode,
                   "stderr": process.stderr[-4000:], "compiled": process.returncode == 0 and obj.is_file(),
                   "authority": "generated compiler experiment, not evidence of original source"}
        if payload["compiled"]:
            payload["object_sha256"] = hashlib.sha256(obj.read_bytes()).hexdigest()
            disassembler = next((shutil.which(p) for p in ("mips-linux-gnu-objdump", "mips64-linux-gnu-objdump") if shutil.which(p)), None)
            if disassembler:
                dis = subprocess.run([disassembler, "-dr", str(obj)], capture_output=True, text=True, timeout=15)
                payload.update(disassembly=dis.stdout[-16000:], disassembly_returncode=dis.returncode)
        text = self._record("compiler-probe", payload)
        (directory / "receipt.json").write_text(text + "\n", encoding="utf-8")
        return text

    def handlers(self):
        return {"inspect_evidence": lambda action: self.evidence(action.query),
                "compiler_probe": lambda action: self.probe(action.source, action.hypothesis),
                "record_hypothesis": self.remember}

    def remember(self, action):
        from solver import shared_hypotheses
        store = {'observations': {r['id']: r for r in self.receipts}}
        key = shared_hypotheses.add(store, subject=action.query,
            alternatives=action.alternatives, support=action.evidence_ids,
            consumers=[self.function], origin=self.function)
        row = store['hypotheses'][key]
        self.hypotheses.append(row)
        return json.dumps({'hypothesis_id': key, 'status': 'proposed',
                           'authority': 'unverified competing explanations'})


def profile(node, model_calls, legacy_profiles):
    """Investigate once per evidence state before proposal-only model work."""
    from solver import repair_queue
    base = repair_queue.next_profile(node, model_calls, legacy_profiles)
    phase = repair_queue.lane(node)
    if phase in {repair_queue.Lane.DONE, repair_queue.Lane.BLOCKED,
                 repair_queue.Lane.INTAKE, repair_queue.Lane.VALIDATE}:
        return base
    key = repair_queue.evidence_key(node)
    used = any(j.get("profile") == "investigate" and j.get("evidence_key") == key
               for j in node.get("jobs", []))
    visits = sum(j.get('profile') == 'investigate' for j in node.get('jobs', []))
    if model_calls and not used and visits < 3 and (base is None or base.get("model") or phase == repair_queue.Lane.ENVIRONMENT):
        return {"name": "investigate", "model": True, "investigate": True,
                "deterministic_budget": 0, "lane": phase.value, "evidence_key": key,
                "brief": json.dumps(question(node))}
    return base
