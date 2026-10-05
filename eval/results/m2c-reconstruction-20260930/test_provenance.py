"""Generated-assembly tests for the opt-in, training-ineligible exporter."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from m2c import main, translate


ASM = """.text
glabel provenance_toy
/* 000000 80001000 8C880004 */ lw $t0, 4($a0)
/* 000004 80001004 00054880 */ sll $t1, $a1, 2
/* 000008 80001008 01091021 */ addu $v0, $t0, $t1
/* 00000C 8000100C 03E00008 */ jr $ra
/* 000010 80001010 00000000 */ nop
"""


def collector_class():
    path = Path(__file__).with_name("provenance.py")
    # Missing exporter should fail for its missing observable capability.
    assert path.exists(), "The expression provenance collector does not exist yet"
    spec = importlib.util.spec_from_file_location("experimental_provenance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ProvenanceCollector


def run_m2c(path):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        rc = main.run(main.parse_flags(["--no-cache", "--passes", "3", str(path)]))
    return rc, output.getvalue()


class ProvenanceTests(unittest.TestCase):
    def test_actual_five_digit_binary_offset_annotation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generated.s"
            path.write_text(ASM.replace("000000 80001000", "9DC20 80001000"))
            with collector_class()() as collector:
                result = run_m2c(path)
            self.assertEqual(result[0], 0)
            load = next(i for i in collector.report()["functions"][0]["instructions"] if i["mnemonic"] == "lw")
            self.assertEqual(load["source_file_offset"], "9DC20")
            self.assertEqual(load["source_address"], "80001000")
            self.assertEqual(load["source_word"], "8C880004")

    def test_real_add_load_shift_return_origins_without_changing_c(self):
        """Catches missing DAG links, missing witnesses, or semantic hook changes."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generated.s"
            path.write_text(ASM)
            baseline = run_m2c(path)
            with collector_class()() as collector:
                observed = run_m2c(path)
            self.assertEqual(baseline[0], 0, baseline[1])
            self.assertEqual(observed, baseline)
            report = collector.report()
            json.dumps(report, allow_nan=False)
            self.assertFalse(report["training_eligible"])
            self.assertEqual(len(report["functions"]), 1)
            function = report["functions"][0]
            self.assertEqual(function["passes_observed"], 3)
            nodes = {node["id"]: node for node in function["expressions"]}
            returned = [root for root in function["roots"] if root["role"] == "return"]
            self.assertTrue(returned)
            reachable = set()
            todo = [root["expression"] for root in returned]
            while todo:
                ident = todo.pop()
                if ident in reachable:
                    continue
                reachable.add(ident)
                todo.extend(edge["expression"] for edge in nodes[ident]["children"])
            operations = {nodes[ident]["shape"].get("op") for ident in reachable}
            self.assertIn("+", operations)
            # m2c's actual expression simplifies sll-by-two to multiplication.
            # The immutable shift witness is independently checked below.
            self.assertIn("*", operations)
            self.assertTrue(any(nodes[ident]["kind"] == "StructAccess" for ident in reachable))
            witnesses = {item["id"]: item for item in function["instructions"]}
            bound = {ref for ident in reachable for ref in nodes[ident]["register_write_witnesses"]}
            self.assertTrue({"lw", "sll", "addu"} <= {witnesses[ref]["mnemonic"] for ref in bound})
            load = next(item for item in witnesses.values() if item["mnemonic"] == "lw")
            self.assertEqual(load["source_line"], "/* 000000 80001000 8C880004 */ lw $t0, 4($a0)")
            self.assertEqual(load["source_address"], "80001000")
            self.assertEqual(load["source_word"], "8C880004")
            self.assertEqual(len(load["source_sha256"]), 64)
            self.assertTrue(all(node["type_hypothesis"]["status"] == "inferred_mutable" for node in nodes.values()))
            self.assertFalse(function["truncation"])

    def test_caps_report_missing_dag_edges_explicitly(self):
        """Catches silently incomplete reports or caps that do not bound storage."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generated.s"
            path.write_text(ASM)
            with collector_class()(max_expressions=2, max_instructions=2) as collector:
                result = run_m2c(path)
            self.assertEqual(result[0], 0, result[1])
            report = collector.report()
            function = report["functions"][0]
            self.assertLessEqual(len(function["expressions"]), 2)
            self.assertLessEqual(len(function["instructions"]), 2)
            self.assertTrue(function["truncation"])
            self.assertFalse(report["origin_coverage_complete"])
            json.dumps(report)

    def test_exception_restores_installed_m2c_hooks(self):
        """Catches hooks leaking into a later baseline invocation."""
        originals = (main.translate_to_ast, translate.evaluate_instruction, translate.RegInfo.set_with_meta)
        with self.assertRaisesRegex(RuntimeError, "intentional"):
            with collector_class()():
                raise RuntimeError("intentional")
        self.assertEqual(originals, (main.translate_to_ast, translate.evaluate_instruction, translate.RegInfo.set_with_meta))

    def test_multi_function_caps_and_source_read_failures_leave_c_unchanged(self):
        """Catches unbounded function retention or a source cap breaking m2c."""
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "one.s"
            second = Path(temp) / "two.s"
            first.write_text(ASM)
            second.write_text(ASM.replace("provenance_toy", "another_toy"))
            def run_both():
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    rc = main.run(main.parse_flags(["--no-cache", str(first), str(second)]))
                return rc, output.getvalue()
            baseline = run_both()
            with collector_class()(max_functions=1, max_source_bytes=8) as collector:
                observed = run_both()
            self.assertEqual(observed, baseline)
            self.assertEqual(observed[0], 0)
            report = collector.report()
            self.assertEqual(len(report["functions"]), 1)
            self.assertIn("functions", report["truncation"])
            self.assertIn("source_bytes", report["truncation"])
            self.assertTrue(all(item["source_sha256"] is None for item in report["functions"][0]["instructions"]))

    def test_observation_failure_does_not_change_generated_c(self):
        """Catches exporter faults being converted into m2c decompilation failures."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "generated.s"
            path.write_text(ASM)
            baseline = run_m2c(path)
            collector = collector_class()()
            def broken_observer(*_):
                raise ValueError("test observation failure")
            collector._instruction = broken_observer
            with collector:
                observed = run_m2c(path)
            self.assertEqual(observed, baseline)
            report = collector.report()
            self.assertIn("ValueError", report["functions"][0]["observation_errors"])


if __name__ == "__main__":
    unittest.main()
