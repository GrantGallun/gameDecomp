"""Add RECORDED game calls to the semantic panel, one replay per candidate.

Mirrors `eval.captured_panel`: wraps the base panel, replays every candidate the
search proposes against each recording, and merges the result. Two deliberate
differences:

* Recorded failures go FIRST in `feedback`. The semantic prompt shows the model
  `feedback[0]` as its primary counterexample, and a disagreement with what the
  real game did outranks one found in synthesized inputs.
* An unusable recording (the original's own replay did not reproduce it) is
  counted but never turned into a counterexample: it cannot judge anything.

Recorded calls are additional observations, never an exactness verdict.
"""
import hashlib
import json
from pathlib import Path
import subprocess

from solver import evidence_schedule, trace_replay, workspace


def identity(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


ARGUMENT_REGISTERS = ("a0", "a1", "a2", "a3")


def pointer_parameters(source: str, function: str) -> dict[str, tuple[str, str]]:
    """Entry register -> (parameter name, pointee record spelling) for record pointers.

    Stops at the first parameter whose register is not simply the next word
    (float, double or 64-bit), rather than guessing o32 placement past it.
    """
    import re
    from solver import repair_context
    match, _end = repair_context.definition(source, function)
    parameters = [p.strip() for p in match.group(2).split(",") if p.strip() and p.strip() != "void"]
    result = {}
    for register, parameter in zip(ARGUMENT_REGISTERS, parameters):
        if re.search(r"\b(?:float|double|f32|f64|s64|u64|long\s+long)\b", parameter) and "*" not in parameter:
            break
        pointer = re.fullmatch(r"(?:const\s+)?((?:struct|union)\s+\w+|\w+)\s*\*\s*(\w+)", parameter)
        if pointer:
            result[register] = (pointer.group(2), pointer.group(1))
    return result


def parameter_fields(repo, ws, source: str, function: str, target: str) -> dict:
    """Compiler-measured layouts of the candidate's own pointer parameters."""
    from solver import type_constraints
    parameters = pointer_parameters(source, function)
    records = sorted({record for _name, record in parameters.values()
                      if record not in {"void", "char", "u8", "s8", "u16", "s16", "u32", "s32", "f32", "f64",
                                        "int", "short", "unsigned"}})
    if not records:
        return {}
    layouts = type_constraints.measure(repo, ws, source, function, target, records=tuple(records[:8]))["layouts"]
    return {register: {"parameter": name, "record": record, "rows": layouts[record]}
            for register, (name, record) in parameters.items() if layouts.get(record)}


class Panel:
    def __init__(self, base, repo, ws, function, recordings):
        self.base, self.repo, self.ws, self.function = base, Path(repo), Path(ws), function
        self.recordings = [r for r in recordings if r.get("function") == function]
        self.cache, self._context, self._fields = {}, None, {}

    @property
    def report(self):
        return {**self.base.report, "recordings": [identity(r) for r in self.recordings]}

    def context(self):
        """Target assembly, arities and return registers, as the semantic lane derives them."""
        if self._context is None:
            from eval import campaign_data, dag_pipeline_pilot as dag
            from solver import project_headers
            target = workspace.semantic_assembly((self.ws / "target_object_dump_normalized.s").read_text(),
                                                 self.ws / "target.o")
            abi = dag.prototype_info(self.repo, self.function, target)
            arities, _ = dag._call_contracts(self.repo, project_headers.called_functions(target))
            symbols = campaign_data.symbol_map((self.repo / "symbol_addrs.txt").read_text())
            from solver import mips_differential
            # Exact extent from the parsed target: every instruction is one word.
            size = 4 * len(mips_differential.Program.parse("recorded_target", target).instructions)
            self._context = (target, dict(arities), tuple(abi["return_registers"]), symbols, size)
        return self._context

    def fields(self, state):
        """Member names for pointer arguments, cached on the declarations the body sees.

        Returns (fields, note). A layout that cannot be measured is reported in
        the panel result, never silently dropped: the offsets still stand alone.
        """
        from solver import repair_context
        try:
            match, _end = repair_context.definition(state.source, self.function)
        except ValueError as error:
            return {}, f"field names unavailable: {error}"
        key = hashlib.sha256(state.source[:match.end()].encode()).hexdigest()
        if key not in self._fields:
            target = ((getattr(state.attempt, "compiler_recipe", None) or {}).get("target", "")
                      if getattr(state, "attempt", None) is not None else "")
            try:
                self._fields[key] = (parameter_fields(self.repo, self.ws, state.source, self.function, target), "")
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                self._fields[key] = ({}, f"field names unavailable: {str(error)[:300]}")
        return self._fields[key]

    def __call__(self, state):
        original = self.base(state)
        if original is None or not self.recordings:
            return original
        key = hashlib.sha256(state.source.encode()).hexdigest()
        if key in self.cache:
            return self.cache[key]
        target, arities, returns, symbols, size = self.context()
        obj = state.object_path
        candidate = workspace.semantic_assembly(obj.with_name(obj.stem + "_object_dump_normalized.s").read_text(), obj)
        def run(record, fields=None):
            try:
                extent = size
                if record.get("end_address") is not None:
                    extent = record["end_address"] - record["entry_address"]
                    # The parsed object dump is not exactly the symbol size: it
                    # can carry alignment padding past it, and normalization drops
                    # a trailing delay-slot nop (getRacePlayerRankingProgress:
                    # 1272 parsed vs 1276). Anything larger is a different
                    # function extent and the recording cannot judge it.
                    if abs(size - extent) >= 16:
                        raise trace_replay.UnusableRecording("recording extent differs from the current target")
                report = trace_replay.replay(record, target, candidate, entry=record["entry_address"],
                                             size=extent, arities=arities, return_registers=returns,
                                             symbol_map=symbols, fields=fields)
            except (trace_replay.UnusableRecording, ValueError, KeyError) as error:
                report = {"status": "unusable", "reasons": [str(error)], "entry_ordinal": record.get("entry_ordinal")}
            report["recording"] = identity(record)[:16]
            return report

        rows = [run(record) for record in self.recordings]
        field_note = ""
        if any(r["status"] == "failed" for r in rows):
            # Only a failure needs member names; measuring costs a compile.
            fields, field_note = self.fields(state)
            if fields:
                rows = [run(record, fields) if row["status"] == "failed" else row
                        for record, row in zip(self.recordings, rows)]
        failed = [r for r in rows if r["status"] == "failed"]
        passed = [r for r in rows if r["status"] == "passed"]
        counts = dict(original.get("counts", {}))
        counts["recorded_failed"], counts["recorded_passed"] = len(failed), len(passed)
        counts["recorded_unusable"] = sum(r["status"] == "unusable" for r in rows)
        status = original["status"]
        if failed:
            status = "observed_failure"
        elif passed and status in ("unavailable", "inconclusive"):
            status = "observed_pass_with_execution_debt"
        # Distance breaks ties between equally failing candidates, so a repair
        # that fixes half the recorded differences is progress, not a stall.
        remaining = sum(r.get("distance") or 0 for r in failed)
        counts["recorded_distance"] = remaining
        result = {**original, "source_sha256": key, "status": status, "counts": counts,
                  "semantic_key": [-len(failed), -remaining, len(passed), *original.get("semantic_key", [])],
                  "recorded_call_results": rows, "unrecorded_panel_status": original["status"],
                  "panel_sha256": evidence_schedule.fingerprint({"base": original.get("panel_sha256"),
                                                                "recordings": [identity(r) for r in self.recordings]}),
                  "authoritative": False, **({"recorded_field_names": field_note} if field_note else {})}
        result["feedback"] = [*[trace_replay.feedback_item(r) for r in failed], *original.get("feedback", [])]
        if passed and not failed:
            result["debt"] = [*original.get("debt", []),
                              f"{len(passed)} recorded game calls behave identically (paths the recording took only)"]
        self.cache[key] = result
        return result
