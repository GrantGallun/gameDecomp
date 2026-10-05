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
import tempfile

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
    def __init__(self, repo, ws, conn, function, output, *, panel=None,
                 advanced=False, project=None, issues=None):
        self.repo, self.ws, self.conn = Path(repo), Path(ws), conn
        self.function, self.output = function, Path(output)
        self.receipts = []
        self.hypotheses = []
        self.capability_tasks = []
        self.advanced, self.project, self.issues = advanced, project, issues or {}
        self.execution = None
        if advanced:
            from solver.execution_experiment import Tools as ExecutionTools
            self.execution = ExecutionTools(repo, ws, function, self.output / 'execution', panel=panel)

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
        neighbors = {}
        if {'kind', 'target_addr'} <= set(columns):
            from eval import callgraph
            outgoing, incoming = callgraph.edges(self.conn)
            neighbors = {'callees': sorted(outgoing.get(query, ()))[:32],
                         'callers': sorted(incoming.get(query, ()))[:32]}
        return self._record("binary-observations", {"query": query, "address": row[0],
            **neighbors,
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
        # Native temporary storage avoids compiling on Windows-mounted output
        # paths. Immutable inputs/results are copied into the receipt directory.
        with tempfile.TemporaryDirectory(prefix='investigation-compiler-') as temporary:
            c, obj = Path(temporary) / 'probe.c', Path(temporary) / 'probe.o'
            c.write_text(source, encoding='utf-8')
            command = [*recipe['command'], '-o', str(obj), str(c)]
            process = subprocess.run(command, cwd=self.repo, capture_output=True, text=True, timeout=45)
            payload = {'prediction': hypothesis, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                       'recipe': recipe, 'command': command, 'returncode': process.returncode,
                       'stderr': process.stderr[-4000:], 'compiled': process.returncode == 0 and obj.is_file(),
                       'authority': 'generated compiler experiment, not evidence of original source'}
            shutil.copy2(c, directory / 'probe.c')
            if payload['compiled']:
                payload['object_sha256'] = hashlib.sha256(obj.read_bytes()).hexdigest()
                shutil.copy2(obj, directory / 'probe.o')
                disassembler = next((shutil.which(p) for p in ('mips-linux-gnu-objdump', 'mips64-linux-gnu-objdump') if shutil.which(p)), None)
                if disassembler:
                    dis = subprocess.run([disassembler, '-dr', str(obj)], capture_output=True, text=True, timeout=15)
                    payload.update(disassembly=dis.stdout[-16000:], disassembly_returncode=dis.returncode)
        text = self._record("compiler-probe", payload)
        (directory / "receipt.json").write_text(text + "\n", encoding="utf-8")
        return text

    def handlers(self):
        handlers = {"inspect_evidence": lambda action: self.evidence(action.query),
                "compiler_probe": lambda action: self.probe(action.source, action.hypothesis),
                "record_hypothesis": self.remember}
        if self.advanced:
            handlers.update(inspect_compiler=self.inspect_compiler,
                            inspect_capabilities=self.inspect_capabilities,
                            execution_case=self.execution_case,
                            request_capability=self.request_capability)
        return handlers

    def inspect_capabilities(self, action):
        from solver import capability_requests
        if self.project is None:
            raise ValueError('capability catalog needs a pinned project')
        return self._record('capability-catalog', capability_requests.catalog(Path(self.project), action.query))

    def inspect_compiler(self, action, candidate):
        from solver import compiler_experiment
        report = compiler_experiment.inspect(self.repo, self.ws, self.function,
            candidate.source, self.output / 'phases', hypothesis=action.hypothesis,
            object_path=candidate.object_path)
        self.output.mkdir(parents=True, exist_ok=True)
        artifact = self.output / ('phase-' + evidence_schedule.fingerprint(report) + '.json')
        artifact.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        assembly = report.get('assembly') or {}
        compact = {key: report.get(key) for key in ('status', 'comparable', 'reason', 'source_sha256',
                   'reproduction', 'recipe', 'compile_units')}
        compact.update(prediction=action.hypothesis, receipt=str(artifact),
            pre_as1=assembly.get('text', '')[:3000],
            pre_as1_truncated=bool(assembly.get('text_truncated') or len(assembly.get('text', '')) > 3000),
            assembly_sha256=assembly.get('sha256'),
            diagnostics=[{'returncode': row.get('returncode'), 'error': row.get('error'),
                          'stderr': row.get('stderr', '')[:400]} for row in report.get('invocations', [])])
        return self._record('compiler-phase-observation', compact)

    def execution_case(self, action, candidate):
        report = self.execution.propose(action.payload, candidate.source, candidate.object_path)
        # Full traces stay in the receipt; put the outcome before bounded details.
        compact = {k: report.get(k) for k in ('status', 'reason', 'comparison', 'reasons',
                   'first_divergence', 'input', 'receipt', 'scope', 'source_sha256',
                   'object_sha256', 'target_sha256', 'panel_sha256')}
        compact['prediction'] = action.hypothesis
        for side in ('target', 'candidate'):
            compact[side] = {k: (report.get(side) or {}).get(k)
                            for k in ('status', 'error', 'return_values', 'abi_violations')}
        return self._record('execution-experiment', compact)

    def request_capability(self, action, candidate):
        from solver import capability_requests
        if self.project is None:
            raise ValueError('engineering requires a pinned project and shared issue context')
        task = capability_requests.propose({**action.payload, 'hypothesis': action.hypothesis},
            issues=self.issues, project=Path(self.project), function=self.function)
        self.capability_tasks.append(task)
        return self._record('capability-request', {'task': task, 'status': 'proposed',
            'authority': 'task description only; isolated reproduction and regression checks still required'})

    def evaluate(self, state, base_panel):
        """Additional finite cases may disprove; they cannot waive existing debt."""
        base = base_panel(state) if base_panel else None
        if not self.execution or not self.execution.cases:
            return base
        observed = self.execution.evaluate(state.source, state.object_path)
        result = dict(base or {'status': 'unavailable', 'reason': 'base panel unavailable',
                              'source_sha256': hashlib.sha256(state.source.encode()).hexdigest()})
        result['investigation_cases'] = {k: observed.get(k) for k in ('status', 'receipt', 'scope')}
        failures = [r for r in observed.get('cases', []) if r['comparison'] == 'failed']
        if failures:
            result['status'] = 'observed_failure'
            result['reason'] = 'target-completed investigation case differs'
            result['feedback'] = [{'kind': 'investigation-counterexample',
                'input': r['input'], 'reasons': r['reasons'], 'first_divergence': r['first_divergence'],
                'receipt': observed['receipt']} for r in failures[:2]] + list(result.get('feedback', []))[:1]
        return result

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


def compiler_identity(repo, recipe):
    """Bind notebook observations to the complete pinned native IDO tool set."""
    directory = Path(repo) / 'tools' / 'ido-recomp' / 'linux'
    binaries = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.glob('*')) if path.is_file()}
    binaries.setdefault('cc', 'missing-file')
    return evidence_schedule.fingerprint({'recipe': recipe or {}, 'ido_recomp_linux': binaries})


def profile(node, model_calls, legacy_profiles, policy=None, binary_revision=None):
    """Investigate once per evidence state before proposal-only model work."""
    from solver import repair_queue
    base = repair_queue.next_profile(node, model_calls, legacy_profiles, binary_revision)
    phase = repair_queue.lane(node)
    if phase in {repair_queue.Lane.DONE, repair_queue.Lane.BLOCKED,
                 repair_queue.Lane.INTAKE, repair_queue.Lane.VALIDATE}:
        return base
    key = repair_queue.evidence_key(node)
    revision = (evidence_schedule.fingerprint(
        {'policy': policy, 'binary_input_revision': binary_revision})
        if policy and binary_revision is not None else
        evidence_schedule.fingerprint(policy) if policy else None)
    used = any(j.get("profile") == "investigate" and j.get("evidence_key") == key
               and j.get('investigation_revision') == revision
               for j in node.get("jobs", []))
    visits = sum(j.get('profile') == 'investigate' and j.get('investigation_revision') == revision
                 for j in node.get('jobs', []))
    if model_calls and not used and visits < 3 and (base is None or base.get("model") or phase == repair_queue.Lane.ENVIRONMENT):
        return {"name": "investigate", "model": True, "investigate": True,
                "deterministic_budget": 0, "lane": phase.value, "evidence_key": key,
                "brief": json.dumps(question(node)),
                **({'investigation_policy': policy, 'investigation_revision': revision,
                    'binary_input_revision': binary_revision} if policy else {})}
    return base


def policy(turns=12, compiles=16, seconds=900):
    if any(type(value) is not int for value in (turns, compiles, seconds)) or not (
            1 <= turns <= 32 and 1 <= compiles <= 64 and 1 <= seconds <= 3600):
        raise ValueError('investigation budgets: 1-32 turns, 1-64 compiles, 1-3600 seconds')
    return {'revision': 'compiler-investigation-v2', 'turns': turns,
            'compiles': compiles, 'seconds': seconds}
