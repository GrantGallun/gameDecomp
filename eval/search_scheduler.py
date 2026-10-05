"""Online counterpart to search_replay, with fixed parent-local mutation streams.

No production search defaults are changed. Callers provide the existing compiler
and repair generator and own external attempt logging. The world is also a full
attempt log; an optional checkpoint persists each result and explicit exhaustion.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
import time

from eval.search_replay import digest, validate_world


class Online:
    def __init__(self, source, compile_candidate, variants, context, *, max_depth=4, checkpoint=None):
        self.source = source
        self.compile_candidate = compile_candidate
        self.variants = variants
        self.max_depth = max_depth
        self.checkpoint = checkpoint
        self.world = {"context": deepcopy(context), "max_depth": max_depth, "nodes": [], "closed": []}
        validate_world(self.world)
        if digest(source) != context["initial_sha256"]:
            raise ValueError("initial source mismatch")
        self.nodes, self.streams, self.seen, self.positions = {}, {}, {}, {}
        try:
            self._takes_evidence = "evidence" in inspect.signature(variants).parameters
        except (TypeError, ValueError):
            self._takes_evidence = False

    def _checkpoint(self):
        validate_world(self.world)
        if self.checkpoint:
            self.checkpoint(self.world)

    def _compile(self, source, label, family, parent, ordinal):
        parent_receipt = self.nodes[parent]["verdict"].get("receipt_id") if parent is not None else None
        started = time.monotonic()
        try:
            verdict = deepcopy(self.compile_candidate(source, label, parent_receipt))
        except Exception as exc:
            verdict = {"compiled": False, "exact": False, "score": 0.0,
                       "stderr": f"{type(exc).__name__}: {exc}", "error": "compile-callback-exception"}
        # Unified diff headers contain filesystem timestamps. They are not
        # compiler feedback and must not make equivalent streams conflict.
        if verdict.get("diff"):
            verdict["raw_diff"] = verdict["diff"]
            verdict["diff"] = "".join(
                line.split("\t", 1)[0] + ("\n" if line.endswith("\n") else "")
                if line.startswith(("--- ", "+++ ")) and "\t" in line else line
                for line in verdict["diff"].splitlines(keepends=True))
        verdict.setdefault("seconds", time.monotonic() - started)
        node = {"id": "root" if parent is None else f"{parent}/{ordinal}",
                "parent": parent, "ordinal": ordinal, "source": source,
                "source_sha256": digest(source), "label": label, "family": family,
                "parent_receipt_id": parent_receipt, "verdict": verdict}
        self.world["nodes"].append(node)
        self.nodes[node["id"]] = node
        self._checkpoint()
        return node

    def start(self):
        if self.nodes:
            raise ValueError("online environment is single-use")
        return self._compile(self.source, "baseline", "baseline", None, 0)

    def expand(self, parent):
        node = self.nodes[parent]
        if (not node["verdict"]["compiled"] or node["verdict"]["exact"]
                or parent.count("/") >= self.max_depth):
            raise ValueError("ineligible expansion")
        if parent in self.world["closed"]:
            return None
        if parent not in self.streams:
            # A generator that declares `evidence` also receives the parent's verdict (source attribution,
            # frontend diagnostics); two-argument generators are called exactly as before.
            extra = {"evidence": node["verdict"]} if self._takes_evidence else {}
            self.streams[parent] = iter(self.variants(node["source"], node["verdict"].get("diff", ""), **extra))
            ancestors, cursor = set(), node
            while cursor is not None:
                ancestors.add(cursor["source_sha256"])
                cursor = self.nodes[cursor["parent"]] if cursor["parent"] is not None else None
            self.seen[parent] = ancestors
        for label, family, source in self.streams[parent]:
            sha = digest(source)
            if sha in self.seen[parent]:
                continue
            self.seen[parent].add(sha)
            ordinal = self.positions.get(parent, 0)
            self.positions[parent] = ordinal + 1
            return self._compile(source, label, family, parent, ordinal)
        self.world["closed"].append(parent)
        self._checkpoint()
        return None
