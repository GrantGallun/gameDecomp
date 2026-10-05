"""Throwaway, opt-in m2c expression provenance observer. TRAINING-INELIGIBLE.

Usage (in an isolated, single-threaded m2c process)::

    with ProvenanceCollector() as collector:
        status = m2c.main.run(m2c.main.parse_flags(argv))
    report = collector.report()

No m2c source files are changed. Hooks forward the original arguments/return
values, never format/use expressions, and retain the last translation pass of
each function. A witness means an instruction wrote this expression value to a
register, NOT that every subexpression was created by that instruction. Child
links come from actual expression attributes (including transparent wrappers),
not parsed C. There is deliberately no claim of complete transitive provenance.
"""

import hashlib
from pathlib import Path
import re


class ProvenanceCollector:
    def __init__(self, *, max_functions=128, max_expressions=2048,
                 max_instructions=4096, max_roots=4096, max_depth=64,
                 max_source_bytes=2_000_000, max_source_files=128,
                 max_text=2048, max_children=64, max_witnesses=64):
        limits = locals().copy()
        limits.pop("self")
        if any(type(value) is not int or value < 1 for value in limits.values()):
            raise ValueError("All provenance limits must be positive integers")
        self.limits = limits
        self._functions = {}
        self._passes = {}
        self._sources = {}
        self._function_sources = {}
        self._active = None
        self._installed = False
        self._global_truncation = set()

    def install(self):
        from m2c import main, translate
        if self._installed or hasattr(main.translate_to_ast, "_provenance_owner"):
            raise RuntimeError("Provenance hooks require an isolated non-nested process")
        self._main, self._translate = main, translate
        self._originals = (main.translate_to_ast, translate.translate_to_ast,
                           translate.evaluate_instruction, translate.RegInfo.set_with_meta,
                           main.parse_file)
        original_ast, _, original_eval, original_set, original_parse = self._originals

        def parse(stream, *args, **kwargs):
            path = getattr(stream, "name", None)
            if isinstance(path, str):
                # Read a separate descriptor; do not change the parser's stream.
                self._source(path)
            result = original_parse(stream, *args, **kwargs)
            for function in result.functions:
                if len(self._function_sources) < self.limits["max_functions"]:
                    self._function_sources[function.name] = path
            return result

        def ast(function, *args, **kwargs):
            name = function.name
            previous = self._active
            if name not in self._functions and len(self._functions) >= self.limits["max_functions"]:
                self._global_truncation.add("functions")
                self._active = None
            else:
                self._passes[name] = self._passes.get(name, 0) + 1
                self._active = {"name": name, "objects": {}, "ids": {}, "instructions": {},
                                "witnesses": {}, "roots": [], "truncation": set(), "info": None,
                                "translation_error": None, "observation_errors": set()}
                self._functions[name] = self._active
            current = self._active
            try:
                result = original_ast(function, *args, **kwargs)
                if current is not None:
                    current["info"] = result
                return result
            except Exception as error:
                if current is not None:
                    current["translation_error"] = type(error).__name__
                raise
            finally:
                self._active = previous

        def evaluate(instr_ref, state):
            if self._active is not None and not self._active["observation_errors"]:
                try:
                    self._instruction(instr_ref.instruction)
                except Exception as error:
                    self._active["observation_errors"].add(type(error).__name__)
            return original_eval(instr_ref, state)

        def set_with_meta(regs, key, value, meta):
            result = original_set(regs, key, value, meta)
            if self._active is not None and not self._active["observation_errors"]:
                try:
                    witness = self._instruction(regs.current_instr())
                    ident = self._expression(value)
                    if ident is not None:
                        self._add_witness(ident, witness)
                        # The wrapper's contents are the very value observed written.
                        # This is a value association, not a creation-site assertion.
                        wrapped = getattr(value, "wrapped_expr", None)
                        if wrapped is not None:
                            child = self._expression(wrapped)
                            if child is not None:
                                self._add_witness(child, witness)
                        self._root("register_write", ident, register=str(key), instruction=witness)
                except Exception as error:
                    self._active["observation_errors"].add(type(error).__name__)
            return result

        ast._provenance_owner = self
        main.translate_to_ast = ast
        translate.translate_to_ast = ast
        translate.evaluate_instruction = evaluate
        translate.RegInfo.set_with_meta = set_with_meta
        main.parse_file = parse
        self._installed = True
        return self

    def uninstall(self):
        if self._installed:
            main, translate = self._main, self._translate
            (main.translate_to_ast, translate.translate_to_ast,
             translate.evaluate_instruction, translate.RegInfo.set_with_meta,
             main.parse_file) = self._originals
            self._installed = False

    def __enter__(self):
        return self.install()

    def __exit__(self, *_):
        self.uninstall()

    def _text(self, value, current=None):
        if len(value) > self.limits["max_text"]:
            (current["truncation"] if current else self._global_truncation).add("text")
            return value[:self.limits["max_text"]]
        return value

    def _source(self, filename):
        if filename in self._sources:
            return self._sources[filename]
        if len(self._sources) >= self.limits["max_source_files"]:
            self._global_truncation.add("source_files")
            return {"error": "source_file_cap", "lines": [], "sha256": None}
        try:
            with Path(filename).open("rb") as stream:
                data = stream.read(self.limits["max_source_bytes"] + 1)
            if len(data) > self.limits["max_source_bytes"]:
                self._global_truncation.add("source_bytes")
                source = {"error": "source_byte_cap", "lines": [], "sha256": None}
            else:
                source = {"error": None, "lines": data.decode("utf-8", errors="replace").splitlines(),
                          "sha256": hashlib.sha256(data).hexdigest()}
        except (OSError, ValueError) as error:
            source = {"error": type(error).__name__, "lines": [], "sha256": None}
        self._sources[filename] = source
        return source

    def _instruction(self, instruction):
        current = self._active
        meta = instruction.meta
        key = (meta.filename, meta.lineno, meta.synthetic, str(instruction))
        if key in current["instructions"]:
            return current["instructions"][key]["id"]
        if len(current["instructions"]) >= self.limits["max_instructions"]:
            current["truncation"].add("instructions")
            return None
        source_path = self._function_sources.get(current["name"]) or meta.filename
        source = self._source(source_path)
        line = source["lines"][meta.lineno - 1] if 0 < meta.lineno <= len(source["lines"]) else None
        address = re.search(r"/\*\s*([0-9A-Fa-f]{1,8})\s+([0-9A-Fa-f]{8})\s+([0-9A-Fa-f]{8})\s*\*/", line or "")
        ident = "i" + str(len(current["instructions"]))
        widths = {"lb": 8, "lbu": 8, "lh": 16, "lhu": 16, "lw": 32, "lwu": 32,
                  "ld": 64, "sb": 8, "sh": 16, "sw": 32, "sd": 64,
                  "lwc1": 32, "ldc1": 64, "swc1": 32, "sdc1": 64}
        current["instructions"][key] = {
            "id": ident, "mnemonic": instruction.mnemonic,
            "normalized_instruction": self._text(str(instruction), current),
            "filename": self._text(meta.filename, current), "lineno": meta.lineno,
            "source_path": self._text(source_path, current),
            "synthetic": meta.synthetic, "source_sha256": source["sha256"],
            "source_line": self._text(line, current) if line else None,
            "source_error": source["error"],
            "source_address": address.group(2) if address else None,
            "source_file_offset": address.group(1) if address else None,
            "source_word": address.group(3) if address else None,
            "observed_operation": {"is_load": instruction.is_load, "is_store": instruction.is_store,
                                   "is_return": instruction.is_return,
                                   "memory_width_bits": widths.get(instruction.mnemonic),
                                   "mips_alu_width_bits": 32 if instruction.mnemonic in
                                   {"add", "addu", "addi", "addiu", "sub", "subu", "sll", "srl", "sra"}
                                   else None},
            "evidence_status": "source_snapshot" if line else "normalized_only",
        }
        return ident

    def _expression(self, expression):
        if not isinstance(expression, self._translate.Expression):
            return None
        current = self._active
        key = id(expression)
        if key in current["ids"]:
            return current["ids"][key]
        if len(current["objects"]) >= self.limits["max_expressions"]:
            current["truncation"].add("expressions")
            return None
        ident = "e" + str(len(current["objects"]))
        current["ids"][key] = ident
        current["objects"][ident] = expression
        current["witnesses"][ident] = set()
        return ident

    def _add_witness(self, ident, witness):
        if witness is None:
            return
        refs = self._active["witnesses"][ident]
        if len(refs) < self.limits["max_witnesses"]:
            refs.add(witness)
        elif witness not in refs:
            self._active["truncation"].add("witnesses")

    def _root(self, role, ident, **extra):
        current = self._active
        if len(current["roots"]) < self.limits["max_roots"]:
            root = {"role": role, "expression": ident, **extra}
            if root not in current["roots"]:
                current["roots"].append(root)
        else:
            current["truncation"].add("roots")

    def _children(self, expression):
        children = []
        for field, value in vars(expression).items():
            if isinstance(value, self._translate.Expression):
                children.append((field, value))
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    if isinstance(item, self._translate.Expression):
                        children.append((f"{field}[{index}]", item))
        if len(children) > self.limits["max_children"]:
            self._active["truncation"].add("children")
        return children[:self.limits["max_children"]]

    def _type(self, expression):
        hypothesis = {"status": "inferred_mutable", "size_bits": None,
                      "signed": None, "category": "unknown"}
        type_ = getattr(expression, "type", None)
        if type_ is not None:
            hypothesis["size_bits"] = type_.get_size_bits()
            if type_.is_signed():
                hypothesis["signed"] = True
            elif type_.is_unsigned():
                hypothesis["signed"] = False
            for category, method in (("pointer", "is_pointer"), ("integer", "is_int"), ("float", "is_float")):
                if getattr(type_, method)():
                    hypothesis["category"] = category
                    break
        return hypothesis

    def report(self):
        """Read retained final-pass objects; call after m2c finishes emitting C."""
        if self._active is not None:
            raise RuntimeError("Cannot report while translation is active")
        functions = []
        for current in self._functions.values():
            self._active = current
            try:
                info = current["info"]
                if info is not None:
                    for node in info.flow_graph.nodes:
                        block = node.block.block_info
                        if block is None:
                            continue
                        for role, expr in (("return", block.return_value), ("branch", block.branch_condition)):
                            ident = self._expression(expr)
                            if ident is not None:
                                self._root(role, ident)
                        for statement in block.to_write:
                            for _, expr in self._children(statement):
                                ident = self._expression(expr)
                                if ident is not None:
                                    self._root("statement", ident)
                depths = {ident: 0 for ident in current["objects"]}
                nodes = []
                index = 0
                while index < len(current["objects"]):
                    ident, expr = list(current["objects"].items())[index]
                    index += 1
                    edges = []
                    incomplete = False
                    for field, child in self._children(expr):
                        if depths[ident] >= self.limits["max_depth"]:
                            current["truncation"].add("depth")
                            incomplete = True
                            continue
                        child_id = self._expression(child)
                        if child_id is None:
                            incomplete = True
                            continue
                        depths.setdefault(child_id, depths[ident] + 1)
                        edges.append({"field": field, "expression": child_id})
                    shape = {}
                    for field in ("op", "value", "offset", "target_size", "c_symbol_name",
                                  "transparent", "trivial", "silent", "reinterpret"):
                        value = getattr(expr, field, None)
                        if isinstance(value, (str, int, float, bool)):
                            shape[field] = self._text(value, current) if isinstance(value, str) else value
                    nodes.append({"id": ident, "kind": type(expr).__name__, "shape": shape,
                                  "children": edges, "children_incomplete": incomplete,
                                  "type_hypothesis": self._type(expr),
                                  "register_write_witnesses": sorted(current["witnesses"][ident]),
                                  "origin_coverage": "partial_observation"})
                functions.append({"name": self._text(current["name"], current),
                                  "passes_observed": self._passes[current["name"]],
                                  "pass_policy": "last_translation_only",
                                  "translation_error": current["translation_error"],
                                  "observation_errors": sorted(current["observation_errors"]),
                                  "expressions": nodes, "roots": current["roots"],
                                  "instructions": list(current["instructions"].values()),
                                  "truncation": sorted(current["truncation"])})
            finally:
                self._active = None
        return {"schema": "m2c-experimental-provenance-v1", "training_eligible": False,
                "origin_coverage_complete": False, "limits": self.limits,
                "truncation": sorted(self._global_truncation), "functions": functions,
                "limitations": ["Register-write witnesses are value associations, not creation sites.",
                                "Child links are observed attributes; phi/control/memory origins may be incomplete.",
                                "Synthetic instruction normalization may combine or rewrite original operations.",
                                "Types and symbolic field/array interpretations are retractable m2c hypotheses.",
                                "Single-threaded isolated-process use only; no output expression formatting is invoked."]}
